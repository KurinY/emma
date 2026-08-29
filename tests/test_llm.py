"""Tests for AnthropicLanguageModel retry behaviour.

The real Anthropic client is never called: anthropic.AsyncAnthropic is patched
at the module level so every test controls exactly which exception is raised and
how many times the underlying create() is attempted.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import anthropic
import httpx
import pytest

from core.llm import AnthropicLanguageModel, LLMUnavailableError

FAKE_KEY = "sk-ant-test"
FAKE_MODEL = "claude-test"
MESSAGES = [{"role": "user", "content": "hi"}]
SYSTEM = "You are a test assistant."


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _raw_reply(text: str = "ok") -> MagicMock:
    """Build a minimal fake Anthropic API response."""
    block = MagicMock()
    block.type = "text"
    block.text = text
    raw = MagicMock()
    raw.content = [block]
    raw.stop_reason = "end_turn"
    raw.usage.input_tokens = 1
    raw.usage.output_tokens = 1
    return raw


def _connection_error() -> anthropic.APIConnectionError:
    return anthropic.APIConnectionError(
        request=httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    )


def _auth_error() -> anthropic.AuthenticationError:
    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    resp = httpx.Response(401, request=req)
    return anthropic.AuthenticationError("invalid key", response=resp, body=None)


def _server_error() -> anthropic.InternalServerError:
    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    resp = httpx.Response(500, request=req)
    return anthropic.InternalServerError("server down", response=resp, body=None)


def _bad_request_error() -> anthropic.BadRequestError:
    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    resp = httpx.Response(400, request=req)
    return anthropic.BadRequestError("bad input", response=resp, body=None)


@pytest.fixture()
def mock_create():
    """Patch anthropic.AsyncAnthropic so no real HTTP calls are made."""
    with patch("core.llm.anthropic.AsyncAnthropic") as mock_class:
        instance = MagicMock()
        instance.messages.create = AsyncMock()
        instance.close = AsyncMock()
        mock_class.return_value = instance
        yield instance.messages.create


def _llm(max_attempts: int = 3) -> AnthropicLanguageModel:
    return AnthropicLanguageModel(
        api_key=FAKE_KEY,
        model=FAKE_MODEL,
        max_attempts=max_attempts,
        backoff_seconds=0,  # no sleeping in tests
    )


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #


async def test_successful_call_returns_text(mock_create):
    mock_create.return_value = _raw_reply("hello")

    response = await _llm().complete(system=SYSTEM, messages=MESSAGES)

    assert response.text == "hello"
    assert mock_create.call_count == 1


async def test_connection_error_is_retried_up_to_max_attempts(mock_create):
    mock_create.side_effect = _connection_error()

    with pytest.raises(LLMUnavailableError):
        await _llm(max_attempts=3).complete(system=SYSTEM, messages=MESSAGES)

    assert mock_create.call_count == 3


async def test_server_error_is_retried(mock_create):
    mock_create.side_effect = _server_error()

    with pytest.raises(LLMUnavailableError):
        await _llm(max_attempts=2).complete(system=SYSTEM, messages=MESSAGES)

    assert mock_create.call_count == 2


async def test_authentication_error_is_not_retried(mock_create):
    mock_create.side_effect = _auth_error()

    with pytest.raises(LLMUnavailableError):
        await _llm(max_attempts=3).complete(system=SYSTEM, messages=MESSAGES)

    assert mock_create.call_count == 1


async def test_bad_request_error_is_not_retried(mock_create):
    mock_create.side_effect = _bad_request_error()

    with pytest.raises(LLMUnavailableError):
        await _llm(max_attempts=3).complete(system=SYSTEM, messages=MESSAGES)

    assert mock_create.call_count == 1


async def test_retry_succeeds_on_second_attempt(mock_create):
    mock_create.side_effect = [_connection_error(), _raw_reply("recovered")]

    response = await _llm(max_attempts=3).complete(system=SYSTEM, messages=MESSAGES)

    assert response.text == "recovered"
    assert mock_create.call_count == 2
