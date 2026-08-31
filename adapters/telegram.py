"""Telegram channel adapter.

This module is the *only* one that knows about Telegram.  Its job is narrow and
mechanical: translate an incoming Telegram update into an
:class:`~core.router.AssistantRequest`, hand it to the router, and deliver the
resulting :class:`~core.router.AssistantResponse` back to the chat.

Two properties are worth calling out:

* **Long polling, not webhooks.**  The bot dials out to Telegram and keeps the
  connection open; nothing listens on a public port, so the server needs no
  inbound firewall rule, no domain and no TLS certificate.
* **Single-user whitelist.**  Anyone can find a bot by its handle, so every
  update is checked against ``TELEGRAM_ALLOWED_USER_ID`` and silently dropped
  when it does not match.  Silence, rather than a refusal, is deliberate: it
  gives a stranger nothing to work with.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import TypeVar

from telegram import Update
from telegram.constants import ChatAction
from telegram.error import BadRequest, NetworkError, TimedOut
from telegram.ext import (
    Application,
    ApplicationBuilder,
    ContextTypes,
    MessageHandler,
    filters,
)

from core.retry import DEFAULT_MAX_ATTEMPTS, pause_before_retry
from core.router import AssistantRequest, Router

logger = logging.getLogger(__name__)

#: Telegram refuses messages longer than 4096 UTF-16 code units.  We split a
#: little earlier to stay clear of the boundary.
MAX_MESSAGE_LENGTH = 4000

#: Attempts for a call to Telegram.  The policy itself lives in core.retry:
#: the model client and this adapter fail for the same kind of reason, and had
#: been given the same answer written out twice.
DEFAULT_SEND_ATTEMPTS = DEFAULT_MAX_ATTEMPTS

#: Last resort, for a fault nobody anticipated.  The router turns every failure
#: it knows about into an answer, so reaching this message means a bug rather
#: than a bad day on the network -- but the user is owed a reply either way, and
#: silence is the one outcome that looks identical to a dead bot.
FALLBACK_INTERNAL = (
    "Ho avuto un problema interno e non sono riuscita a rispondere. L'errore e' stato registrato."
)

_T = TypeVar("_T")


class TelegramAdapter:
    """Runs the Telegram bot on the event loop of the host application."""

    def __init__(self, token: str, allowed_user_id: int, router: Router) -> None:
        """Build the adapter.

        Args:
            token: Bot token issued by BotFather.
            allowed_user_id: The only Telegram user ID served by this bot.
            router: The channel-agnostic orchestrator.
        """
        self._allowed_user_id = allowed_user_id
        self._router = router
        self._application: Application = (
            ApplicationBuilder().token(token).build()  # type: ignore[assignment]
        )
        self._application.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self._on_text_message)
        )
        self._application.add_error_handler(self._on_error)

    async def start(self) -> None:
        """Start long polling.

        Pending updates accumulated while the service was down are dropped: on
        a restart the user wants a live assistant, not a burst of replies to
        questions they asked hours ago.
        """
        await self._application.initialize()
        await self._application.start()
        if self._application.updater is None:  # pragma: no cover - defensive
            raise RuntimeError("the Telegram application was built without an updater")
        await self._application.updater.start_polling(
            drop_pending_updates=True,
            allowed_updates=[Update.MESSAGE],
        )
        logger.info("telegram adapter started (long polling)")

    async def stop(self) -> None:
        """Stop polling and release the Telegram resources, in reverse order.

        Every step is attempted even when the one before it failed. They were
        chained, so a single raise skipped all the rest and left the HTTP
        session and PTB's task queue half-released on a process already on its
        way out. It is the same defect the application lifespan had, one level
        up, and it is likelier here: about one connection in twenty to Telegram
        fails from this host, and shutdown is exactly when one gets dropped.
        """
        steps: list[tuple[str, Callable[[], Awaitable[None]]]] = []
        updater = self._application.updater
        if updater is not None and updater.running:
            steps.append(("polling", updater.stop))
        if self._application.running:
            steps.append(("application", self._application.stop))
        steps.append(("resources", self._application.shutdown))

        for name, close in steps:
            try:
                await close()
            except Exception:  # one stubborn step must not strand the rest
                logger.exception("could not stop the telegram %s cleanly", name)

        logger.info("telegram adapter stopped")

    async def _on_text_message(self, update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle one incoming text message.

        Args:
            update: The Telegram update.
            _context: PTB call context; unused, the adapter is stateless.
        """
        message = update.effective_message
        user = update.effective_user
        chat = update.effective_chat
        if message is None or user is None or chat is None or not message.text:
            return

        if user.id != self._allowed_user_id:
            logger.warning("ignored message from user_id=%s (not in whitelist)", user.id)
            return

        logger.info("incoming message from chat_id=%s (%d chars)", chat.id, len(message.text))

        # The typing indicator is decoration.  It used to be the first call out
        # to Telegram, so a network blip on it killed the turn before the model
        # was even consulted and the user got nothing at all -- a failure
        # indistinguishable, from the phone, from a dead bot.
        try:
            await chat.send_chat_action(ChatAction.TYPING)
        except Exception as exc:  # never worth a lost reply
            logger.warning("could not show the typing indicator: %s", exc)

        response = await self._router.handle(
            AssistantRequest(
                text=message.text,
                user_id=str(user.id),
                conversation_id=str(chat.id),
            )
        )

        delivered = 0
        for chunk in _split_message(response.text):
            if await self._send(lambda c=chunk: message.reply_text(c)):
                delivered += 1

        if delivered == 0:
            logger.error(
                "answer to chat_id=%s was never delivered (%d chars lost)",
                chat.id,
                len(response.text),
            )
            return

        logger.info(
            "answered chat_id=%s (%d chars, degraded=%s)",
            chat.id,
            len(response.text),
            response.degraded,
        )

    async def _send(self, call: Callable[[], Awaitable[_T]]) -> bool:
        """Perform one call to Telegram, retrying while the failure is transient.

        Roughly one connection in twenty to Telegram fails from an IPv6-only
        host with no IPv4 to fall back on, measured on the production server.
        Without a retry that ratio is also the share of answers the user never
        sees, and the loss is silent: the reply is simply gone.

        Only genuinely transient failures are retried.  A rejected message --
        too long, wrong chat, bot blocked -- will be rejected identically three
        times, so retrying it only delays the log line that explains it.

        **Mind the order of the handlers.**  In ``python-telegram-bot``
        ``BadRequest`` inherits from ``NetworkError``, so catching the latter
        catches permanent rejections too; the specific clause has to come
        first, or a message Telegram will never accept is sent three times.

        Args:
            call: A zero-argument callable performing the Telegram request.

        Returns:
            ``True`` when the call succeeded.  A failure is logged here rather
            than raised: one lost chunk should not discard the ones that did
            arrive, nor take down the handler.
        """
        for attempt in range(1, DEFAULT_SEND_ATTEMPTS + 1):
            try:
                await call()
                if attempt > 1:
                    logger.info("telegram send succeeded on attempt %d", attempt)
                return True
            except BadRequest:
                # Permanent, despite inheriting from NetworkError.
                logger.exception("telegram rejected the message permanently")
                return False
            except (TimedOut, NetworkError) as exc:
                logger.warning(
                    "telegram send failed (attempt %d/%d): %s: %s",
                    attempt,
                    DEFAULT_SEND_ATTEMPTS,
                    type(exc).__name__,
                    exc,
                )
                await pause_before_retry(attempt, DEFAULT_SEND_ATTEMPTS)
            except Exception:
                logger.exception("telegram rejected the message permanently")
                return False
        return False

    async def _on_error(self, update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Log any exception escaping a handler, and still answer if we can.

        The router turns every failure it knows about into an answer, so an
        exception arriving here is a bug rather than a bad day on the network.
        That makes it more worth reporting, not less: the process survives
        either way, but without this the user gets silence, which from a phone
        looks exactly like a bot that has died.

        Args:
            update: The update being processed, if there was one.
            context: PTB context carrying the exception.
        """
        logger.error("unhandled error while processing an update", exc_info=context.error)

        chat = getattr(update, "effective_chat", None)
        user = getattr(update, "effective_user", None)
        # The whitelist governs this path too. An error handler is exactly the
        # place where a check gets forgotten, and the bug that put us here may
        # be the reason a stranger's update reached a handler at all.
        if chat is None or user is None or user.id != self._allowed_user_id:
            return
        await self._send(lambda: chat.send_message(FALLBACK_INTERNAL))


def _split_message(text: str, limit: int = MAX_MESSAGE_LENGTH) -> list[str]:
    """Split ``text`` into chunks Telegram will accept.

    The split prefers a newline near the end of the chunk, so paragraphs and
    code blocks are not cut mid-line whenever avoidable.

    Args:
        text: The message to send.
        limit: Maximum size of a chunk, in characters.

    Returns:
        A non-empty list of chunks; a short message yields a single one.
    """
    if not text:
        return [""]

    chunks: list[str] = []
    remaining = text
    while len(remaining) > limit:
        window = remaining[:limit]
        cut = window.rfind("\n")
        if cut < limit // 2:
            # No convenient newline: hard cut at the limit.
            chunks.append(remaining[:limit].rstrip())
            remaining = remaining[limit:]
        else:
            # If the cut landed on the second \n of a blank line (\n\n),
            # step back one so the blank line is kept intact at the start
            # of the next chunk rather than being consumed by rstrip.
            if cut > 0 and window[cut - 1] == "\n":
                cut -= 1
            chunks.append(remaining[:cut].rstrip())
            remaining = remaining[cut + 1 :]  # skip exactly the split newline
    chunks.append(remaining)
    return chunks
