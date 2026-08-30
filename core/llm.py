"""LLM clients with a shared retry policy.

This module is the only place in the project that imports provider SDKs.
It exposes:

* :data:`Message` -- the shape of a conversation message on the wire;
* :class:`TextBlock` / :class:`ToolUseBlock` -- the two kinds of content the
  router has to deal with, decoupled from the SDK's own classes;
* :class:`LanguageModel` -- the narrow interface the router depends on;
* :class:`AnthropicLanguageModel` -- Anthropic implementation with exponential
  backoff and a hard cap on the number of attempts;
* :class:`GroqLanguageModel` -- Groq implementation (OpenAI-compatible API)
  with the same retry policy;
* :class:`LLMUnavailableError` -- raised once every attempt has failed, so the
  caller can turn it into a polite message instead of a crash.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

import anthropic

logger = logging.getLogger(__name__)

#: A conversation message in Anthropic wire format: ``{"role": ..., "content": ...}``
#: where ``content`` is either a string or a list of content blocks.  Using the
#: wire format directly keeps the router free of translation code while still
#: hiding the SDK's Python objects behind this module.
Message = dict[str, Any]

#: Maximum number of tokens the model may produce in a single reply.  Generous
#: enough for a chat answer, small enough to bound cost and latency.
DEFAULT_MAX_TOKENS = 2048

#: How many times a single logical call is attempted before giving up.
DEFAULT_MAX_ATTEMPTS = 3

#: Delay before the second attempt, in seconds.  It doubles at every further
#: attempt: 1s, then 2s.
DEFAULT_BACKOFF_SECONDS = 1.0

#: Per-request timeout, in seconds.  Without it a hung connection would keep a
#: Telegram handler waiting forever.
DEFAULT_TIMEOUT_SECONDS = 60.0


class LLMError(RuntimeError):
    """Base class for every error raised by this module."""


class LLMUnavailableError(LLMError):
    """The model could not be reached after exhausting all attempts."""


@dataclass(frozen=True, slots=True)
class TextBlock:
    """A chunk of assistant prose.

    Attributes:
        text: The text produced by the model.
    """

    text: str


@dataclass(frozen=True, slots=True)
class ToolUseBlock:
    """A request from the model to run one tool.

    Attributes:
        id: Identifier the matching ``tool_result`` must carry.
        name: Name of the tool to run.
        input: Arguments, already decoded from JSON by the SDK.
    """

    id: str
    name: str
    input: dict[str, Any]


#: Either kind of block the router knows how to handle.
ContentBlock = TextBlock | ToolUseBlock


@dataclass(frozen=True, slots=True)
class LLMResponse:
    """One reply from the model.

    Attributes:
        blocks: Content blocks, in the order the model produced them.
        stop_reason: Why generation stopped; ``"tool_use"`` means the router
            has to run the requested tools and call back.
    """

    blocks: tuple[ContentBlock, ...]
    stop_reason: str | None

    @property
    def text(self) -> str:
        """Return the concatenated prose of the reply, without tool blocks."""
        return "\n\n".join(b.text for b in self.blocks if isinstance(b, TextBlock)).strip()

    @property
    def tool_uses(self) -> tuple[ToolUseBlock, ...]:
        """Return the tool invocations requested by the model, in order."""
        return tuple(b for b in self.blocks if isinstance(b, ToolUseBlock))

    def to_assistant_message(self) -> Message:
        """Rebuild the assistant message to send back in the next request.

        The Messages API is stateless: to continue an agentic turn we must
        replay the assistant reply verbatim, tool blocks included.

        Returns:
            A message ready to be appended to the request history.
        """
        content: list[dict[str, Any]] = []
        for block in self.blocks:
            if isinstance(block, TextBlock):
                content.append({"type": "text", "text": block.text})
            else:
                content.append(
                    {
                        "type": "tool_use",
                        "id": block.id,
                        "name": block.name,
                        "input": block.input,
                    }
                )
        return {"role": "assistant", "content": content}


@runtime_checkable
class LanguageModel(Protocol):
    """The slice of a chat model the router actually needs."""

    async def complete(
        self,
        *,
        system: str,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        """Produce one reply.

        Args:
            system: The system prompt (the assistant personality).
            messages: Conversation history in wire format, oldest first.
            tools: JSON-schema declarations of the callable tools, if any.

        Returns:
            The model reply.

        Raises:
            LLMUnavailableError: If the model could not be reached.
        """
        ...


class AnthropicLanguageModel:
    """:class:`LanguageModel` backed by the official Anthropic SDK.

    Retries are handled here and only here: the SDK's own retry loop is turned
    off (``max_retries=0``) so that the number of attempts is exactly the one
    configured on this object and stays visible in the logs.
    """

    def __init__(
        self,
        api_key: str,
        model: str,
        *,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        backoff_seconds: float = DEFAULT_BACKOFF_SECONDS,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        """Build a client.

        Args:
            api_key: Anthropic API key.
            model: Model identifier, e.g. ``claude-haiku-4-5-20251001``.
            max_tokens: Cap on the length of a single reply.
            max_attempts: Total attempts per logical call, retries included.
            backoff_seconds: Delay before the second attempt; it doubles after
                every further failure.
            timeout_seconds: Per-request timeout.

        Raises:
            ValueError: If ``max_attempts`` is smaller than one.
        """
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        self._client = anthropic.AsyncAnthropic(
            api_key=api_key,
            timeout=timeout_seconds,
            max_retries=0,
        )
        self._model = model
        self._max_tokens = max_tokens
        self._max_attempts = max_attempts
        self._backoff_seconds = backoff_seconds

    async def complete(
        self,
        *,
        system: str,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        """Call the Messages API, retrying transient failures.

        Args:
            system: The system prompt.
            messages: Conversation history in wire format, oldest first.
            tools: Tool declarations, or ``None`` when no tool is registered.

        Returns:
            The model reply, converted to this module's own block types.

        Raises:
            LLMUnavailableError: If every attempt failed.
        """
        payload: dict[str, Any] = {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "system": system,
            "messages": messages,
        }
        if tools:
            payload["tools"] = tools

        last_error: Exception | None = None
        for attempt in range(1, self._max_attempts + 1):
            try:
                raw = await self._client.messages.create(**payload)
            except anthropic.APIConnectionError as exc:
                # Network-level failure: transient, retry.
                last_error = exc
                logger.warning(
                    "anthropic call failed (attempt %d/%d): %s: %s",
                    attempt,
                    self._max_attempts,
                    type(exc).__name__,
                    exc,
                )
                if attempt < self._max_attempts:
                    # 1s, then 2s, then 4s...  Exponential, so a short outage
                    # is absorbed without hammering the API.
                    await asyncio.sleep(self._backoff_seconds * 2 ** (attempt - 1))
                continue
            except anthropic.APIStatusError as exc:
                if exc.status_code >= 500:
                    # Server-side error: transient, retry.
                    last_error = exc
                    logger.warning(
                        "anthropic call failed (attempt %d/%d): %s: %s",
                        attempt,
                        self._max_attempts,
                        type(exc).__name__,
                        exc,
                    )
                    if attempt < self._max_attempts:
                        await asyncio.sleep(self._backoff_seconds * 2 ** (attempt - 1))
                    continue
                # 4xx: permanent failure (wrong key, bad request, etc.).
                # Retrying cannot help; surface it immediately.
                logger.error(
                    "anthropic call rejected permanently (attempt %d/%d): HTTP %s %s: %s",
                    attempt,
                    self._max_attempts,
                    exc.status_code,
                    type(exc).__name__,
                    exc,
                )
                raise LLMUnavailableError(
                    f"the Anthropic API returned a permanent error: {exc}"
                ) from exc
            except anthropic.AnthropicError as exc:
                # Any other SDK error that is not connection- or status-based.
                logger.error(
                    "anthropic call failed with non-retryable error (attempt %d/%d): %s: %s",
                    attempt,
                    self._max_attempts,
                    type(exc).__name__,
                    exc,
                )
                raise LLMUnavailableError(
                    f"the Anthropic API raised a non-retryable error: {exc}"
                ) from exc

            logger.info(
                "anthropic call ok (attempt %d): stop_reason=%s in=%d out=%d",
                attempt,
                raw.stop_reason,
                raw.usage.input_tokens,
                raw.usage.output_tokens,
            )
            return _to_response(raw)

        raise LLMUnavailableError(
            f"the Anthropic API did not answer after {self._max_attempts} attempts"
        ) from last_error

    async def aclose(self) -> None:
        """Release the underlying HTTP connection pool."""
        await self._client.close()


class GroqLanguageModel:
    """:class:`LanguageModel` backed by the Groq API (OpenAI-compatible).

    Uses the same retry policy as :class:`AnthropicLanguageModel`: transient
    errors (connection failures, 5xx) are retried with exponential backoff;
    permanent errors (4xx) are surfaced immediately.

    The system prompt is injected as the first message (role ``"system"``),
    which is the OpenAI convention Groq uses.
    """

    def __init__(
        self,
        api_key: str,
        model: str,
        *,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        backoff_seconds: float = DEFAULT_BACKOFF_SECONDS,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        import groq as groq_sdk

        self._groq_sdk = groq_sdk
        self._client = groq_sdk.AsyncGroq(api_key=api_key)
        self._model = model
        self._max_tokens = max_tokens
        self._max_attempts = max_attempts
        self._backoff_seconds = backoff_seconds

    async def complete(
        self,
        *,
        system: str,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        """Call the Groq chat completions API, retrying transient failures."""
        groq_messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
        for m in messages:
            content = m["content"]
            groq_messages.append(
                {
                    "role": m["role"],
                    "content": content if isinstance(content, str) else _flatten_content(content),
                }
            )

        last_error: Exception | None = None
        for attempt in range(1, self._max_attempts + 1):
            try:
                raw = await self._client.chat.completions.create(
                    model=self._model,
                    messages=groq_messages,
                    max_tokens=self._max_tokens,
                )
            except self._groq_sdk.APIConnectionError as exc:
                last_error = exc
                logger.warning(
                    "groq call failed (attempt %d/%d): %s: %s",
                    attempt,
                    self._max_attempts,
                    type(exc).__name__,
                    exc,
                )
                if attempt < self._max_attempts:
                    await asyncio.sleep(self._backoff_seconds * 2 ** (attempt - 1))
                continue
            except self._groq_sdk.APIStatusError as exc:
                if exc.status_code >= 500:
                    last_error = exc
                    logger.warning(
                        "groq call failed (attempt %d/%d): %s: %s",
                        attempt,
                        self._max_attempts,
                        type(exc).__name__,
                        exc,
                    )
                    if attempt < self._max_attempts:
                        await asyncio.sleep(self._backoff_seconds * 2 ** (attempt - 1))
                    continue
                logger.error(
                    "groq call rejected permanently (attempt %d/%d): HTTP %s %s: %s",
                    attempt,
                    self._max_attempts,
                    exc.status_code,
                    type(exc).__name__,
                    exc,
                )
                raise LLMUnavailableError(
                    f"the Groq API returned a permanent error: {exc}"
                ) from exc

            choice = raw.choices[0]
            text = choice.message.content or ""
            stop_reason = choice.finish_reason
            logger.info(
                "groq call ok (attempt %d): stop_reason=%s in=%d out=%d",
                attempt,
                stop_reason,
                raw.usage.prompt_tokens,
                raw.usage.completion_tokens,
            )
            return LLMResponse(blocks=(TextBlock(text=text),), stop_reason=stop_reason)

        raise LLMUnavailableError(
            f"the Groq API did not answer after {self._max_attempts} attempts"
        ) from last_error

    async def aclose(self) -> None:
        """Release the underlying HTTP connection pool."""
        await self._client.close()


def _flatten_content(content: Any) -> str:
    """Convert Anthropic-style content blocks to a plain string.

    Used when replaying assistant messages to a non-Anthropic backend that
    expects plain-text content.  Only ``text`` blocks are included; tool
    blocks are dropped because Groq handles tools differently.
    """
    if isinstance(content, list):
        return "\n\n".join(
            block["text"]
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
    return str(content)


def _to_response(raw: anthropic.types.Message) -> LLMResponse:
    """Convert an SDK message into an :class:`LLMResponse`.

    Unknown block types (for instance thinking blocks, which version 1 does not
    request) are skipped rather than raising, so that a future API addition
    cannot break the running assistant.

    Args:
        raw: The object returned by the SDK.

    Returns:
        The same reply expressed with this module's own types.
    """
    blocks: list[ContentBlock] = []
    for block in raw.content:
        if block.type == "text":
            blocks.append(TextBlock(text=block.text))
        elif block.type == "tool_use":
            arguments = block.input if isinstance(block.input, dict) else {}
            blocks.append(ToolUseBlock(id=block.id, name=block.name, input=arguments))
        else:
            logger.debug("ignoring unsupported content block of type %s", block.type)
    return LLMResponse(blocks=tuple(blocks), stop_reason=raw.stop_reason)
