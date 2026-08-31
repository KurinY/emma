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

import json
import logging
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

import anthropic

from core.retry import (
    DEFAULT_BACKOFF_SECONDS,
    DEFAULT_MAX_ATTEMPTS,
    pause_before_retry,
)

logger = logging.getLogger(__name__)

#: A conversation message in Anthropic wire format: ``{"role": ..., "content": ...}``
#: where ``content`` is either a string or a list of content blocks.  Using the
#: wire format directly keeps the router free of translation code while still
#: hiding the SDK's Python objects behind this module.
Message = dict[str, Any]

#: Maximum number of tokens the model may produce in a single reply.  Generous
#: enough for a chat answer, small enough to bound cost and latency.
DEFAULT_MAX_TOKENS = 2048

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
                await pause_before_retry(attempt, self._max_attempts, self._backoff_seconds)
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
                    await pause_before_retry(attempt, self._max_attempts, self._backoff_seconds)
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

    Tool use is supported and translated in both directions by
    :func:`_to_groq_tools`, :func:`_to_groq_messages` and
    :func:`_from_groq_message`.  The router only ever speaks the Anthropic
    dialect; keeping the dialects apart is this class's job, and the reason the
    router did not have to change when Groq was added.
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
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": _to_groq_messages(system, messages),
            "max_tokens": self._max_tokens,
        }
        if tools:
            payload["tools"] = _to_groq_tools(tools)

        last_error: Exception | None = None
        for attempt in range(1, self._max_attempts + 1):
            try:
                raw = await self._client.chat.completions.create(**payload)
            except self._groq_sdk.APIConnectionError as exc:
                last_error = exc
                logger.warning(
                    "groq call failed (attempt %d/%d): %s: %s",
                    attempt,
                    self._max_attempts,
                    type(exc).__name__,
                    exc,
                )
                await pause_before_retry(attempt, self._max_attempts, self._backoff_seconds)
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
                    await pause_before_retry(attempt, self._max_attempts, self._backoff_seconds)
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
            response = _from_groq_message(choice.message, choice.finish_reason)
            logger.info(
                "groq call ok (attempt %d): stop_reason=%s in=%d out=%d tools=%d",
                attempt,
                response.stop_reason,
                raw.usage.prompt_tokens,
                raw.usage.completion_tokens,
                len(response.tool_uses),
            )
            return response

        raise LLMUnavailableError(
            f"the Groq API did not answer after {self._max_attempts} attempts"
        ) from last_error

    async def aclose(self) -> None:
        """Release the underlying HTTP connection pool."""
        await self._client.close()


def _text_of(content: Any) -> str:
    """Return the prose of Anthropic-style content, ignoring non-text blocks."""
    if isinstance(content, list):
        return "\n\n".join(
            block["text"]
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
    return str(content)


def _to_groq_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Translate tool declarations from the router's shape into Groq's.

    The router speaks the Anthropic dialect -- ``name``, ``description``,
    ``input_schema`` at the top level -- because that is the protocol its
    agentic loop was written against.  The OpenAI-compatible APIs nest the same
    information under ``function`` and call the schema ``parameters``.

    Doing the translation here is the point of the adapter: the router keeps one
    vocabulary and every backend meets it where it is.

    Args:
        tools: Declarations as :meth:`core.router.Router._tool_schemas` builds
            them.

    Returns:
        The same declarations in the shape Groq expects.
    """
    return [
        {
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool["description"],
                "parameters": tool.get("input_schema") or {"type": "object", "properties": {}},
            },
        }
        for tool in tools
    ]


def _to_groq_messages(system: str, messages: list[Message]) -> list[dict[str, Any]]:
    """Translate the conversation from the router's shape into Groq's.

    The two protocols disagree about where tool traffic lives, and an agentic
    turn cannot be replayed without honouring the difference:

    * a request to run a tool is a ``tool_use`` **content block** for Anthropic,
      and a ``tool_calls`` **field on the assistant message** for Groq, with the
      arguments as a JSON *string* rather than an object;
    * a result is a ``tool_result`` block inside a user message for Anthropic,
      and a message of its own with ``role: "tool"`` for Groq, one per call.

    Flattening any of that to prose -- which is what this adapter used to do --
    leaves the model unable to see that it ever called anything, so the second
    round of a tool turn starts from nothing.

    Args:
        system: The system prompt.
        messages: History in the router's Anthropic-shaped wire format.

    Returns:
        Messages ready for the chat completions API.
    """
    out: list[dict[str, Any]] = [{"role": "system", "content": system}]

    for message in messages:
        role = message["role"]
        content = message["content"]

        if isinstance(content, str):
            out.append({"role": role, "content": content})
            continue

        blocks = [b for b in content if isinstance(b, dict)]

        if role == "assistant":
            text = _text_of(blocks)
            calls = [
                {
                    "id": b["id"],
                    "type": "function",
                    # Groq wants the arguments as a JSON string, not an object.
                    "function": {
                        "name": b["name"],
                        "arguments": json.dumps(b.get("input") or {}, ensure_ascii=False),
                    },
                }
                for b in blocks
                if b.get("type") == "tool_use"
            ]
            reply: dict[str, Any] = {"role": "assistant"}
            # A turn that is only tool calls carries no prose, and the API wants
            # the content explicitly null rather than an empty string.
            reply["content"] = text or None
            if calls:
                reply["tool_calls"] = calls
            out.append(reply)
            continue

        # A user message may carry tool results, ordinary text, or both. The
        # results have to become separate messages, and they must directly
        # follow the assistant turn that asked for them.
        prose: list[str] = []
        for block in blocks:
            kind = block.get("type")
            if kind == "tool_result":
                out.append(
                    {
                        "role": "tool",
                        "tool_call_id": block["tool_use_id"],
                        "content": str(block.get("content", "")),
                    }
                )
            elif kind == "text":
                prose.append(block["text"])
        if prose:
            out.append({"role": "user", "content": "\n\n".join(prose)})

    return out


def _from_groq_message(message: Any, finish_reason: str | None) -> LLMResponse:
    """Convert a Groq reply into the blocks the router understands.

    Args:
        message: ``raw.choices[0].message`` from the SDK.
        finish_reason: ``raw.choices[0].finish_reason``.

    Returns:
        The reply in this module's own vocabulary, with ``stop_reason`` mapped
        to ``"tool_use"`` when tools were requested -- which is the value the
        router's loop looks for.
    """
    blocks: list[ContentBlock] = []

    text = getattr(message, "content", None)
    if text:
        blocks.append(TextBlock(text=text))

    for call in getattr(message, "tool_calls", None) or ():
        raw_arguments = call.function.arguments or "{}"
        try:
            arguments = json.loads(raw_arguments)
        except (TypeError, ValueError):
            # A model that emits malformed JSON has still asked for the tool.
            # Running it with no arguments and letting it complain beats
            # dropping the call, which would look like the model said nothing.
            logger.warning(
                "tool call '%s' carried arguments that are not JSON: %r",
                call.function.name,
                raw_arguments,
            )
            arguments = {}
        if not isinstance(arguments, dict):
            arguments = {}
        blocks.append(ToolUseBlock(id=call.id, name=call.function.name, input=arguments))

    if not blocks:
        blocks.append(TextBlock(text=""))

    stop_reason = "tool_use" if finish_reason == "tool_calls" else finish_reason
    return LLMResponse(blocks=tuple(blocks), stop_reason=stop_reason)


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
