"""Tests for the shared retry policy.

Small surface, but two callers depend on it and one of them is the path every
answer to the user travels down. The case worth guarding is the last attempt:
pausing after it would add a delay to a failure already decided, which is
precisely the kind of thing nobody notices until someone is waiting.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from core.retry import (
    DEFAULT_BACKOFF_SECONDS,
    DEFAULT_MAX_ATTEMPTS,
    backoff_delay,
    pause_before_retry,
)


@pytest.fixture
def slept(monkeypatch):
    """Record what would have been waited, without waiting."""
    import core.retry

    sleep = AsyncMock()
    monkeypatch.setattr(core.retry.asyncio, "sleep", sleep)
    return sleep


def test_the_delay_doubles():
    assert backoff_delay(1, base=1.0) == 1.0
    assert backoff_delay(2, base=1.0) == 2.0
    assert backoff_delay(3, base=1.0) == 4.0


def test_the_base_is_respected():
    assert backoff_delay(1, base=0.5) == 0.5
    assert backoff_delay(3, base=0.5) == 2.0


def test_the_default_base_is_one_second():
    assert backoff_delay(1) == DEFAULT_BACKOFF_SECONDS


async def test_it_waits_between_attempts(slept):
    await pause_before_retry(1, max_attempts=3, base=1.0)

    slept.assert_awaited_once_with(1.0)


async def test_the_second_wait_is_longer(slept):
    await pause_before_retry(2, max_attempts=3, base=1.0)

    slept.assert_awaited_once_with(2.0)


async def test_it_does_not_wait_after_the_last_attempt(slept):
    """The outcome is already decided; waiting only delays reporting it."""
    await pause_before_retry(3, max_attempts=3)

    slept.assert_not_awaited()


async def test_a_single_attempt_never_waits(slept):
    await pause_before_retry(1, max_attempts=1)

    slept.assert_not_awaited()


async def test_the_whole_sequence_waits_once_less_than_it_tries(slept):
    """Three attempts mean two pauses, not three."""
    for attempt in range(1, DEFAULT_MAX_ATTEMPTS + 1):
        await pause_before_retry(attempt)

    assert slept.await_count == DEFAULT_MAX_ATTEMPTS - 1
