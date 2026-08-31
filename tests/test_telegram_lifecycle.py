"""Tests for starting and stopping the Telegram adapter.

Neither half had a test, which is awkward for the same reason `main.py` had
none: a fault here does not degrade one reply, it decides whether the bot
answers at all, or whether the process can exit.

`stop()` carried the defect the application lifespan carried until this
evening -- three steps chained, so the first to raise skipped the rest. It
matters more here than it looks. About one connection in twenty to Telegram
fails from this host, and shutdown is exactly the moment a connection gets
dropped, so the failing path is the ordinary one rather than the exotic one.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from adapters.telegram import TelegramAdapter

ALLOWED_USER = 4242


@pytest.fixture
def adapter():
    """An adapter whose PTB application is entirely mocked."""
    with patch("adapters.telegram.ApplicationBuilder") as builder:
        application = MagicMock()
        application.initialize = AsyncMock()
        application.start = AsyncMock()
        application.stop = AsyncMock()
        application.shutdown = AsyncMock()
        application.updater.start_polling = AsyncMock()
        application.updater.stop = AsyncMock()
        application.updater.running = True
        application.running = True
        builder.return_value.token.return_value.build.return_value = application

        built = TelegramAdapter(token="123:AA", allowed_user_id=ALLOWED_USER, router=MagicMock())
        built._application = application
        yield built


# --------------------------------------------------------------------------- #
# Starting
# --------------------------------------------------------------------------- #


async def test_starting_brings_up_polling(adapter):
    await adapter.start()

    adapter._application.initialize.assert_awaited_once()
    adapter._application.start.assert_awaited_once()
    adapter._application.updater.start_polling.assert_awaited_once()


async def test_updates_waiting_from_before_the_restart_are_dropped(adapter):
    """On a restart the user wants a live assistant, not a burst of old replies."""
    await adapter.start()

    assert adapter._application.updater.start_polling.await_args.kwargs["drop_pending_updates"]


async def test_an_application_with_no_updater_says_so(adapter):
    """Rather than an AttributeError three frames deeper."""
    adapter._application.updater = None

    with pytest.raises(RuntimeError, match="updater"):
        await adapter.start()


# --------------------------------------------------------------------------- #
# Stopping
# --------------------------------------------------------------------------- #


async def test_stopping_releases_everything_in_reverse(adapter):
    await adapter.stop()

    adapter._application.updater.stop.assert_awaited_once()
    adapter._application.stop.assert_awaited_once()
    adapter._application.shutdown.assert_awaited_once()


async def test_a_failing_step_does_not_strand_the_ones_after_it(adapter):
    """The defect: a dropped connection while stopping skipped the rest."""
    adapter._application.updater.stop.side_effect = RuntimeError("connection reset")

    await adapter.stop()

    adapter._application.stop.assert_awaited_once()
    adapter._application.shutdown.assert_awaited_once()


async def test_the_last_step_runs_even_when_both_before_it_fail(adapter):
    """shutdown() is the one that actually releases the HTTP session."""
    adapter._application.updater.stop.side_effect = RuntimeError("reset")
    adapter._application.stop.side_effect = RuntimeError("stuck")

    await adapter.stop()

    adapter._application.shutdown.assert_awaited_once()


async def test_a_failure_while_stopping_is_reported(adapter, caplog):
    adapter._application.updater.stop.side_effect = RuntimeError("connection reset")

    with caplog.at_level("ERROR"):
        await adapter.stop()

    assert any("could not stop the telegram polling" in r.message for r in caplog.records)


async def test_stopping_never_raises(adapter):
    """It runs from the lifespan's finally; raising there loses the other steps."""
    adapter._application.updater.stop.side_effect = RuntimeError("reset")
    adapter._application.stop.side_effect = RuntimeError("stuck")
    adapter._application.shutdown.side_effect = RuntimeError("gone")

    await adapter.stop()  # must not raise


async def test_what_never_started_is_not_stopped(adapter):
    """Stopping a stopped bot must not invent work, but must still release."""
    adapter._application.updater.running = False
    adapter._application.running = False

    await adapter.stop()

    adapter._application.updater.stop.assert_not_awaited()
    adapter._application.stop.assert_not_awaited()
    adapter._application.shutdown.assert_awaited_once()


async def test_an_absent_updater_does_not_stop_the_shutdown(adapter):
    adapter._application.updater = None

    await adapter.stop()

    adapter._application.shutdown.assert_awaited_once()
