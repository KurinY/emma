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
    """Collapse the backoff so the suite stays fast."""
    import adapters.telegram as mod

    monkeypatch.setattr(mod.asyncio, "sleep", AsyncMock())


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
