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
    remaining_backoff,
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


# --------------------------------------------------------------------------- #
# How much waiting is still on the table
# --------------------------------------------------------------------------- #
#
# Asked when the other end says how long to wait: retrying a limit that lasts
# eleven minutes only makes the refusal slower.  The value is a promise about
# what the pauses add up to, so it is checked against them, not restated.


def test_the_budget_is_what_the_remaining_pauses_add_up_to():
    assert remaining_backoff(1, 3, 1.0) == backoff_delay(1, 1.0) + backoff_delay(2, 1.0)


def test_the_budget_shrinks_as_the_attempts_are_spent():
    assert remaining_backoff(1, 3, 1.0) > remaining_backoff(2, 3, 1.0)


def test_the_last_attempt_has_nothing_left_to_wait():
    """It matches ``pause_before_retry``, which also declines to sleep here."""
    assert remaining_backoff(3, 3) == 0.0


def test_a_single_attempt_has_no_budget_at_all():
    assert remaining_backoff(1, 1) == 0.0


def test_the_default_three_attempts_cover_three_seconds():
    """The number the rate-limit decision is actually weighed against."""
    assert remaining_backoff(1) == 3.0
