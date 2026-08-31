"""The project's retry policy, in one place.

Three calls out of this process can fail because a network blinked: the model,
the Telegram send, and the Telegram send again for each chunk of a long answer.
Each of them was written with the same policy -- three attempts, waiting one
second then two -- and each of them wrote that policy out by hand, so the
formula appeared five times across two modules. Changing our mind about it
would have meant remembering all five.

What is deliberately *not* here is the decision of **which** failures deserve a
retry. That is not shared: it depends on the API being called, and getting it
wrong is expensive in both directions. Retrying a permanent rejection wastes
attempts and delays the error that explains itself -- python-telegram-bot
raising ``BadRequest`` as a subclass of ``NetworkError`` cost exactly that. Not
retrying a transient one costs the user an answer. Each caller keeps that
judgement, close to the SDK whose exceptions it has to read.
"""

from __future__ import annotations

import asyncio

#: How many times a call is attempted in total, the first one included. Three
#: absorbs a blink without leaving a person waiting on a failure that is not
#: going to resolve.
DEFAULT_MAX_ATTEMPTS = 3

#: The pause before the second attempt, in seconds. It doubles after that:
#: 1s, then 2s, then 4s. Exponential so that a brief outage is absorbed without
#: hammering an API that may be struggling for everyone.
DEFAULT_BACKOFF_SECONDS = 1.0


def backoff_delay(attempt: int, base: float = DEFAULT_BACKOFF_SECONDS) -> float:
    """Return how long to wait before the attempt after this one.

    Args:
        attempt: The attempt that has just failed, counting from 1.
        base: The pause before the second attempt.

    Returns:
        The delay in seconds, doubling with each attempt.
    """
    return base * 2 ** (attempt - 1)


async def pause_before_retry(
    attempt: int,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    base: float = DEFAULT_BACKOFF_SECONDS,
) -> None:
    """Wait before retrying, or return at once when this was the last attempt.

    Callers used to guard the sleep themselves, and a missing guard is a bug
    that hides well: the last failure would pause pointlessly before reporting
    an outcome that was already decided, adding delay to an answer the user is
    waiting for.

    Args:
        attempt: The attempt that has just failed, counting from 1.
        max_attempts: How many attempts there are in total.
        base: The pause before the second attempt.
    """
    if attempt < max_attempts:
        await asyncio.sleep(backoff_delay(attempt, base))


def remaining_backoff(
    attempt: int,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    base: float = DEFAULT_BACKOFF_SECONDS,
) -> float:
    """Return how long the retries still to come would wait in total.

    Used to answer a question that only arises when the other end tells us how
    long to wait: is retrying worth it at all?  A server asking for eleven
    minutes will not be satisfied by three, and spending the attempts anyway
    only delays a refusal that is already certain -- with someone waiting at
    the other end of it.

    Args:
        attempt: The attempt that has just failed, counting from 1.
        max_attempts: How many attempts there are in total.
        base: The pause before the second attempt.

    Returns:
        The sum of the pauses left, or ``0.0`` when this was the last attempt.
    """
    return sum(backoff_delay(later, base) for later in range(attempt, max_attempts))
