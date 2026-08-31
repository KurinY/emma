"""Tests for getting the answer out, when Telegram does not cooperate.

Roughly one connection in twenty to Telegram fails from the production host --
IPv6 only, no IPv4 to fall back on -- and before this the loss was silent: a
failed send killed the turn and the user saw nothing, which from a phone is
indistinguishable from a dead bot. It happened on 31 August 2026.

The turn is deliberately not all-or-nothing. A long answer is several messages,
and delivering four chunks out of five is worth more than delivering none.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram.error import BadRequest, NetworkError, TimedOut

from adapters.telegram import DEFAULT_SEND_ATTEMPTS, TelegramAdapter
from core.router import AssistantResponse

ALLOWED_USER = 4242


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch):
    """Collapse the backoff so the suite stays fast.

    Patched in :mod:`core.retry`, which is where the waiting now happens: the
    adapter asks for the pause rather than performing it.
    """
    import core.retry

    monkeypatch.setattr(core.retry.asyncio, "sleep", AsyncMock())


def build_adapter(reply: str = "ecco la risposta") -> tuple[TelegramAdapter, MagicMock]:
    """An adapter whose router always answers, with Telegram fully mocked."""
    router = MagicMock()
    router.handle = AsyncMock(return_value=AssistantResponse(text=reply))
    adapter = TelegramAdapter.__new__(TelegramAdapter)
    adapter._router = router
    adapter._allowed_user_id = ALLOWED_USER
    return adapter, router


def build_update(text: str = "ciao") -> MagicMock:
    update = MagicMock()
    update.effective_message.text = text
    update.effective_message.reply_text = AsyncMock()
    update.effective_user.id = ALLOWED_USER
    update.effective_chat.id = 99
    update.effective_chat.send_chat_action = AsyncMock()
    return update


# --------------------------------------------------------------------------- #
# The typing indicator is decoration
# --------------------------------------------------------------------------- #


async def test_a_failed_typing_indicator_does_not_cost_the_answer():
    """It used to be the first call out, so a blip on it lost the whole turn."""
    adapter, _ = build_adapter()
    update = build_update()
    update.effective_chat.send_chat_action.side_effect = TimedOut()

    await adapter._on_text_message(update, MagicMock())

    update.effective_message.reply_text.assert_awaited_once()


async def test_the_indicator_is_still_sent_when_it_works():
    adapter, _ = build_adapter()
    update = build_update()

    await adapter._on_text_message(update, MagicMock())

    update.effective_chat.send_chat_action.assert_awaited_once()


# --------------------------------------------------------------------------- #
# Delivering the answer
# --------------------------------------------------------------------------- #


async def test_a_transient_failure_is_retried_until_it_lands():
    adapter, _ = build_adapter()
    update = build_update()
    update.effective_message.reply_text.side_effect = [TimedOut(), None]

    await adapter._on_text_message(update, MagicMock())

    assert update.effective_message.reply_text.await_count == 2


async def test_a_network_error_is_transient_too():
    adapter, _ = build_adapter()
    update = build_update()
    update.effective_message.reply_text.side_effect = [NetworkError("giù"), None]

    await adapter._on_text_message(update, MagicMock())

    assert update.effective_message.reply_text.await_count == 2


async def test_retrying_stops_at_the_ceiling():
    adapter, _ = build_adapter()
    update = build_update()
    update.effective_message.reply_text.side_effect = TimedOut()

    await adapter._on_text_message(update, MagicMock())

    assert update.effective_message.reply_text.await_count == DEFAULT_SEND_ATTEMPTS


async def test_a_rejection_is_not_retried():
    """Too long, wrong chat, bot blocked: it will be rejected identically."""
    adapter, _ = build_adapter()
    update = build_update()
    update.effective_message.reply_text.side_effect = BadRequest("message too long")

    await adapter._on_text_message(update, MagicMock())

    assert update.effective_message.reply_text.await_count == 1


async def test_a_lost_answer_is_logged_as_an_error(caplog):
    """Silence is what made this hard to see from the phone."""
    adapter, _ = build_adapter()
    update = build_update()
    update.effective_message.reply_text.side_effect = TimedOut()

    with caplog.at_level("ERROR"):
        await adapter._on_text_message(update, MagicMock())

    assert any("never delivered" in r.message for r in caplog.records)


async def test_a_failing_handler_never_raises():
    """An exception here would reach PTB's error handler and end the turn."""
    adapter, _ = build_adapter()
    update = build_update()
    update.effective_message.reply_text.side_effect = TimedOut()

    await adapter._on_text_message(update, MagicMock())  # must not raise


async def test_one_lost_chunk_does_not_discard_the_others():
    """A long answer is several messages; four out of five beats none."""
    adapter, _ = build_adapter("x" * 9000)  # splits into three chunks
    update = build_update()
    # The first chunk exhausts its attempts, the rest go through.
    update.effective_message.reply_text.side_effect = [
        TimedOut(),
        TimedOut(),
        TimedOut(),
        None,
        None,
    ]

    await adapter._on_text_message(update, MagicMock())

    assert update.effective_message.reply_text.await_count == 5


async def test_the_normal_path_sends_once_per_chunk():
    adapter, _ = build_adapter("risposta breve")
    update = build_update()

    await adapter._on_text_message(update, MagicMock())

    update.effective_message.reply_text.assert_awaited_once_with("risposta breve")


async def test_a_stranger_is_still_ignored_before_anything_is_sent():
    adapter, router = build_adapter()
    update = build_update()
    update.effective_user.id = ALLOWED_USER + 1

    await adapter._on_text_message(update, MagicMock())

    router.handle.assert_not_awaited()
    update.effective_message.reply_text.assert_not_awaited()


# --------------------------------------------------------------------------- #
# The last resort
# --------------------------------------------------------------------------- #
#
# The router turns every failure it knows about into an answer, so an exception
# reaching the error handler means a bug. That makes replying more important,
# not less: the process survives either way, but the user gets silence, and
# from a phone silence is indistinguishable from a dead bot.


def build_error_context(exc: Exception | None = None) -> MagicMock:
    context = MagicMock()
    context.error = exc or RuntimeError("boom")
    return context


def build_error_update() -> MagicMock:
    update = MagicMock()
    update.effective_user.id = ALLOWED_USER
    update.effective_chat.send_message = AsyncMock()
    return update


async def test_an_unexpected_fault_still_gets_an_answer():
    adapter, _ = build_adapter()
    update = build_error_update()

    await adapter._on_error(update, build_error_context())

    update.effective_chat.send_message.assert_awaited_once()


async def test_the_apology_is_not_one_of_the_router_messages():
    """It names a bug, not a busy model: the two need different words."""
    from adapters.telegram import FALLBACK_INTERNAL

    adapter, _ = build_adapter()
    update = build_error_update()

    await adapter._on_error(update, build_error_context())

    assert update.effective_chat.send_message.await_args.args[0] == FALLBACK_INTERNAL


async def test_the_whitelist_still_holds_on_the_error_path():
    """The place a check is likeliest to be forgotten, and likeliest to matter.

    Whatever bug sent us here may be the reason a stranger's update reached a
    handler at all.
    """
    adapter, _ = build_adapter()
    update = build_error_update()
    update.effective_user.id = ALLOWED_USER + 1

    await adapter._on_error(update, build_error_context())

    update.effective_chat.send_message.assert_not_awaited()


async def test_an_error_with_no_update_is_only_logged():
    """PTB passes an update of None for faults outside a handler."""
    adapter, _ = build_adapter()

    await adapter._on_error(None, build_error_context())  # must not raise


async def test_the_fault_is_logged_even_when_the_apology_lands(caplog):
    """The reply is for the user; the traceback is the only thing we can debug."""
    adapter, _ = build_adapter()
    update = build_error_update()

    with caplog.at_level("ERROR"):
        await adapter._on_error(update, build_error_context())

    assert any("unhandled error" in r.message for r in caplog.records)


async def test_a_failure_to_apologise_is_survived():
    """Telegram being down is exactly when this path runs; it must not raise."""
    adapter, _ = build_adapter()
    update = build_error_update()
    update.effective_chat.send_message.side_effect = TimedOut()

    await adapter._on_error(update, build_error_context())  # must not raise
