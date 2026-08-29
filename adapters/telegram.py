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

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    ApplicationBuilder,
    ContextTypes,
    MessageHandler,
    filters,
)

from core.router import AssistantRequest, Router

logger = logging.getLogger(__name__)

#: Telegram refuses messages longer than 4096 UTF-16 code units.  We split a
#: little earlier to stay clear of the boundary.
MAX_MESSAGE_LENGTH = 4000


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
        """Stop polling and release the Telegram resources, in reverse order."""
        if self._application.updater is not None and self._application.updater.running:
            await self._application.updater.stop()
        if self._application.running:
            await self._application.stop()
        await self._application.shutdown()
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
        await chat.send_chat_action(ChatAction.TYPING)

        response = await self._router.handle(
            AssistantRequest(
                text=message.text,
                user_id=str(user.id),
                conversation_id=str(chat.id),
            )
        )

        for chunk in _split_message(response.text):
            await message.reply_text(chunk)
        logger.info(
            "answered chat_id=%s (%d chars, degraded=%s)",
            chat.id,
            len(response.text),
            response.degraded,
        )

    async def _on_error(self, _update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Log any exception escaping a handler, keeping the bot alive.

        Args:
            _update: The update being processed, if any.
            context: PTB context carrying the exception.
        """
        logger.error("unhandled error while processing an update", exc_info=context.error)


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
