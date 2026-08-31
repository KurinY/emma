"""Tests for what happens when the model answers, but says no.

On the evening of 31 August 2026 the Groq daily token quota ran out. The log
recorded the reason precisely -- ``Limit 200000, Used 199048`` -- and the user
was told "Non riesco a contattare il cervello, riprova tra poco": the wrong
diagnosis, and the wrong advice, since retrying was the one thing guaranteed
to fail. They spent the evening guessing why the assistant had gone quiet.

Two faults met at that point. A 429 fell into the branch for permanent 4xx
failures, so a per-minute limit that clears in seconds was never retried; and
the message written for an unreachable model was reused for a model that had
replied. Both are covered here, for both providers, because the next quota to
run out will not be the one we happened to test.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import anthropic
import groq
import httpx
import pytest

import core.retry
from core.llm import (
    HTTP_TOO_MANY_REQUESTS,
    AnthropicLanguageModel,
    GroqLanguageModel,
    LLMQuotaExceededError,
    LLMUnavailableError,
    _exhausted,
    _retry_after_seconds,
)
from core.router import (
    FALLBACK_QUOTA,
    FALLBACK_UNAVAILABLE,
    AssistantRequest,
    Router,
    _quota_message,
)

FAKE_KEY = "test-key"
FAKE_MODEL = "test-model"


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch):
    """Keep the real backoff values, spend none of the real seconds.

    The durations matter here -- the decision under test compares the server's
    requested wait against them -- so they are left at their defaults and only
    the sleeping is removed.
    """
    monkeypatch.setattr(core.retry.asyncio, "sleep", AsyncMock())


# --------------------------------------------------------------------------- #
# Building the two clients from one description
# --------------------------------------------------------------------------- #


def rate_limit_error(sdk, retry_after: str | None):
    """A real SDK ``RateLimitError``, carrying the header a server would send."""
    headers = {} if retry_after is None else {"retry-after": retry_after}
    response = httpx.Response(
        HTTP_TOO_MANY_REQUESTS,
        headers=headers,
        request=httpx.Request("POST", "https://example.invalid/v1/messages"),
    )
    return sdk.RateLimitError("rate limit reached", response=response, body=None)


def auth_error(sdk):
    response = httpx.Response(
        401, request=httpx.Request("POST", "https://example.invalid/v1/messages")
    )
    return sdk.AuthenticationError("invalid key", response=response, body=None)


def anthropic_reply() -> MagicMock:
    raw = MagicMock()
    block = MagicMock()
    block.type = "text"
    block.text = "ok"
    raw.content = [block]
    raw.stop_reason = "end_turn"
    raw.usage.input_tokens = 1
    raw.usage.output_tokens = 1
    return raw


def groq_reply() -> SimpleNamespace:
    message = SimpleNamespace(content="ok", tool_calls=None)
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason="stop")],
        usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
    )


PROVIDERS = {
    "anthropic": (anthropic, "core.llm.anthropic.AsyncAnthropic", anthropic_reply),
    "groq": (groq, "groq.AsyncGroq", groq_reply),
}


@pytest.fixture(params=sorted(PROVIDERS))
def client(request):
    """Yield each provider's client in turn, with its SDK fully mocked.

    Both clients are meant to follow one retry policy, and the way they drifted
    apart before -- Groq ignoring tools entirely -- is the argument for testing
    them from a single description rather than twice by hand.
    """
    sdk, target, reply = PROVIDERS[request.param]
    with patch(target) as sdk_class:
        create = AsyncMock()
        instance = MagicMock()
        instance.messages.create = create
        instance.chat.completions.create = create
        instance.close = AsyncMock()
        sdk_class.return_value = instance

        if request.param == "anthropic":
            model = AnthropicLanguageModel(api_key=FAKE_KEY, model=FAKE_MODEL)
        else:
            model = GroqLanguageModel(api_key=FAKE_KEY, model=FAKE_MODEL)

        yield SimpleNamespace(model=model, sdk=sdk, create=create, reply=reply)


async def ask(client) -> object:
    return await client.model.complete(
        system="Sei EMMA.", messages=[{"role": "user", "content": "ciao"}]
    )


# --------------------------------------------------------------------------- #
# Reading the server's advice
# --------------------------------------------------------------------------- #


def test_the_retry_after_header_is_read():
    assert _retry_after_seconds(rate_limit_error(anthropic, "42")) == 42.0


def test_a_missing_header_is_not_a_zero_wait():
    """None means "the server did not say", never "come back now"."""
    assert _retry_after_seconds(rate_limit_error(anthropic, None)) is None


def test_an_unparsable_header_is_treated_as_no_advice():
    """The header may also carry an HTTP date; guessing at it would be worse."""
    stamp = "Wed, 21 Oct 2026 07:28:00 GMT"

    assert _retry_after_seconds(rate_limit_error(anthropic, stamp)) is None


def test_anything_without_a_response_is_tolerated():
    """``_exhausted`` hands over whatever was caught last, including nothing."""
    assert _retry_after_seconds(None) is None
    assert _retry_after_seconds(RuntimeError("connection reset")) is None


# --------------------------------------------------------------------------- #
# Deciding whether retrying can help
# --------------------------------------------------------------------------- #


async def test_a_short_rate_limit_is_retried(client):
    """A per-minute cap clears in seconds: it deserves the same retry as a blip."""
    client.create.side_effect = [rate_limit_error(client.sdk, "1"), client.reply()]

    await ask(client)

    assert client.create.await_count == 2


async def test_a_rate_limit_is_no_longer_a_permanent_failure(client):
    """The original defect, stated directly: 429 fell through with the 4xx."""
    client.create.side_effect = [rate_limit_error(client.sdk, None), client.reply()]

    await ask(client)

    assert client.create.await_count == 2


async def test_a_long_rate_limit_gives_up_at_once(client):
    """The mirror image: spending attempts on a wait of eleven minutes.

    684 seconds is what Groq actually asked for on the night this was found.
    Three retries cover three seconds, and the user is waiting for the refusal.
    """
    client.create.side_effect = rate_limit_error(client.sdk, "684")

    with pytest.raises(LLMQuotaExceededError) as raised:
        await ask(client)

    assert client.create.await_count == 1
    assert raised.value.retry_after == 684.0


async def test_a_silent_rate_limit_is_retried_then_reported_as_quota(client):
    """No advice given: retry, but do not forget what the failure was."""
    client.create.side_effect = rate_limit_error(client.sdk, None)

    with pytest.raises(LLMQuotaExceededError):
        await ask(client)

    assert client.create.await_count == 3


async def test_a_real_4xx_is_still_permanent(client):
    """Widening the retry must not swallow the case it sits next to."""
    client.create.side_effect = auth_error(client.sdk)

    with pytest.raises(LLMUnavailableError) as raised:
        await ask(client)

    assert not isinstance(raised.value, LLMQuotaExceededError)
    assert client.create.await_count == 1


async def test_a_successful_call_is_untouched(client):
    """The path every ordinary message takes, asserted alongside the failures."""
    client.create.side_effect = [client.reply()]

    answer = await ask(client)

    assert answer.text == "ok"
    assert client.create.await_count == 1


# --------------------------------------------------------------------------- #
# Not losing the reason on the way out
# --------------------------------------------------------------------------- #


def test_exhausting_the_attempts_on_a_429_still_says_quota():
    error = _exhausted("Groq", 3, rate_limit_error(anthropic, "30"))

    assert isinstance(error, LLMQuotaExceededError)
    assert error.retry_after == 30.0


def test_exhausting_the_attempts_on_anything_else_says_unavailable():
    error = _exhausted("Groq", 3, RuntimeError("connection reset"))

    assert isinstance(error, LLMUnavailableError)
    assert not isinstance(error, LLMQuotaExceededError)


def test_the_quota_error_is_still_an_unavailable_error():
    """Existing handlers keep working; only the ones that care look closer."""
    assert issubclass(LLMQuotaExceededError, LLMUnavailableError)


# --------------------------------------------------------------------------- #
# What the user is finally told
# --------------------------------------------------------------------------- #


def test_the_quota_message_is_not_the_outage_message():
    """The whole point: one invites a retry, and the other must not."""
    assert _quota_message(None) != FALLBACK_UNAVAILABLE
    assert "riprova tra poco" not in _quota_message(None).lower()


def test_a_short_wait_is_given_in_seconds():
    assert "30 secondi" in _quota_message(30)


def test_a_long_wait_is_given_in_minutes():
    assert "11 minuti" in _quota_message(684)


def test_an_unknown_wait_still_says_what_happened():
    assert FALLBACK_QUOTA in _quota_message(None)


def build_router(error: Exception) -> tuple[Router, MagicMock]:
    llm = MagicMock()
    llm.complete = AsyncMock(side_effect=error)
    memory = MagicMock()
    memory.get_history = AsyncMock(return_value=[])
    memory.append = AsyncMock()
    return Router(llm=llm, memory=memory, system_prompt="Sei EMMA."), memory


async def test_the_router_reports_a_quota_without_claiming_an_outage():
    router, _ = build_router(LLMQuotaExceededError("spent", retry_after=684.0))

    answer = await router.handle(AssistantRequest(conversation_id="1", user_id=1, text="ciao"))

    assert answer.degraded
    assert "11 minuti" in answer.text
    assert answer.text != FALLBACK_UNAVAILABLE


async def test_a_genuine_outage_still_says_so():
    """The branch above is placed first; the one below must still be reachable."""
    router, _ = build_router(LLMUnavailableError("no route to host"))

    answer = await router.handle(AssistantRequest(conversation_id="1", user_id=1, text="ciao"))

    assert answer.text == FALLBACK_UNAVAILABLE


async def test_a_degraded_quota_turn_is_not_remembered():
    """Same reason as any degraded turn: it would poison the window."""
    router, memory = build_router(LLMQuotaExceededError("spent"))

    await router.handle(AssistantRequest(conversation_id="1", user_id=1, text="ciao"))

    memory.append.assert_not_awaited()
