"""Tests for the agentic router.

The model is replaced by :class:`ScriptedModel`, which hands back a canned list
of replies and records what it was asked.  That keeps the suite offline, fast
and deterministic, and it exercises the exact interface the real client
implements.
"""

from __future__ import annotations

from typing import Any, ClassVar

import pytest

from core.llm import LLMResponse, LLMUnavailableError, Message, TextBlock, ToolUseBlock
from core.memory import InMemoryConversationMemory, StoredMessage
from core.router import (
    FALLBACK_EMPTY,
    FALLBACK_TOO_MANY_STEPS,
    FALLBACK_UNAVAILABLE,
    AssistantRequest,
    Router,
)

SYSTEM_PROMPT = "Sei EMMA."


class ScriptedModel:
    """A :class:`~core.llm.LanguageModel` that replays prepared replies."""

    def __init__(self, replies: list[LLMResponse | Exception]) -> None:
        """Store the script and prepare the call log."""
        self._replies = list(replies)
        self.calls: list[list[Message]] = []
        self.tools_seen: list[list[dict[str, Any]] | None] = []

    async def complete(
        self,
        *,
        system: str,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        """Return the next scripted reply, or raise it if it is an exception."""
        assert system == SYSTEM_PROMPT
        # Deep-ish copy: the router extends the same list across tool rounds.
        self.calls.append([dict(message) for message in messages])
        self.tools_seen.append(tools)
        if not self._replies:
            raise AssertionError("the model was called more times than scripted")
        reply = self._replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


class RecordingTool:
    """A tool that records its calls and returns a fixed answer."""

    name = "clock"
    description = "Return the current time."
    input_schema: ClassVar[dict[str, Any]] = {"type": "object", "properties": {}}

    def __init__(self, output: str = "18:30") -> None:
        """Store the canned output and prepare the call log."""
        self.output = output
        self.calls: list[dict[str, Any]] = []

    async def run(self, arguments: dict[str, Any]) -> str:
        """Record the arguments and return the canned output."""
        self.calls.append(arguments)
        return self.output


class ExplodingTool(RecordingTool):
    """A tool that always fails, to prove failures stay contained."""

    name = "broken"

    async def run(self, arguments: dict[str, Any]) -> str:
        """Always raise."""
        raise RuntimeError("boom")


def text_reply(text: str) -> LLMResponse:
    """Build a final, prose-only reply."""
    return LLMResponse(blocks=(TextBlock(text=text),), stop_reason="end_turn")


def tool_reply(name: str, call_id: str = "call-1", **arguments: Any) -> LLMResponse:
    """Build a reply that asks for one tool call."""
    return LLMResponse(
        blocks=(ToolUseBlock(id=call_id, name=name, input=dict(arguments)),),
        stop_reason="tool_use",
    )


def build_router(
    replies: list[LLMResponse | Exception],
    *,
    tools: tuple[Any, ...] = (),
    max_messages: int = 20,
    max_tool_iterations: int = 5,
) -> tuple[Router, ScriptedModel, InMemoryConversationMemory]:
    """Assemble a router around a scripted model and a fresh memory."""
    model = ScriptedModel(replies)
    memory = InMemoryConversationMemory(max_messages=max_messages)
    router = Router(
        llm=model,
        memory=memory,
        system_prompt=SYSTEM_PROMPT,
        tools=tools,
        max_tool_iterations=max_tool_iterations,
    )
    return router, model, memory


def request(text: str = "che ore sono?", conversation_id: str = "chat-1") -> AssistantRequest:
    """Build an incoming request."""
    return AssistantRequest(text=text, user_id="42", conversation_id=conversation_id)


async def test_plain_turn_returns_the_model_text():
    router, model, memory = build_router([text_reply("Sono le 18:30.")])

    response = await router.handle(request())

    assert response.text == "Sono le 18:30."
    assert response.degraded is False
    assert model.calls == [[{"role": "user", "content": "che ore sono?"}]]
    assert await memory.get_history("chat-1") == [
        StoredMessage(role="user", content="che ore sono?"),
        StoredMessage(role="assistant", content="Sono le 18:30."),
    ]


async def test_previous_history_is_replayed_to_the_model():
    router, model, _ = build_router([text_reply("primo"), text_reply("secondo")])

    await router.handle(request("uno"))
    await router.handle(request("due"))

    assert model.calls[1] == [
        {"role": "user", "content": "uno"},
        {"role": "assistant", "content": "primo"},
        {"role": "user", "content": "due"},
    ]


async def test_conversations_do_not_leak_into_each_other():
    router, model, _ = build_router([text_reply("primo"), text_reply("secondo")])

    await router.handle(request("uno", conversation_id="chat-1"))
    await router.handle(request("due", conversation_id="chat-2"))

    assert model.calls[1] == [{"role": "user", "content": "due"}]


async def test_no_tools_declared_when_none_are_registered():
    router, model, _ = build_router([text_reply("ok")])

    await router.handle(request())

    assert model.tools_seen == [None]


async def test_tool_loop_runs_the_tool_and_feeds_the_result_back():
    tool = RecordingTool()
    router, model, memory = build_router(
        [tool_reply("clock", timezone="Europe/Rome"), text_reply("Sono le 18:30.")],
        tools=(tool,),
    )

    response = await router.handle(request())

    assert response.text == "Sono le 18:30."
    assert tool.calls == [{"timezone": "Europe/Rome"}]

    # The tools are declared to the model on every call of the turn...
    assert model.tools_seen[0] == [
        {
            "name": "clock",
            "description": "Return the current time.",
            "input_schema": {"type": "object", "properties": {}},
        }
    ]
    # ...and the second call replays the assistant turn plus the tool result.
    second_call = model.calls[1]
    assert second_call[1]["role"] == "assistant"
    assert second_call[1]["content"][0]["type"] == "tool_use"
    assert second_call[2] == {
        "role": "user",
        "content": [{"type": "tool_result", "tool_use_id": "call-1", "content": "18:30"}],
    }

    # Intermediate blocks are working memory, not conversation: only the user
    # message and the final answer are stored.
    assert await memory.get_history("chat-1") == [
        StoredMessage(role="user", content="che ore sono?"),
        StoredMessage(role="assistant", content="Sono le 18:30."),
    ]


async def test_unknown_tool_is_reported_to_the_model_not_raised():
    router, model, _ = build_router(
        [tool_reply("ghost"), text_reply("Non ho quello strumento.")],
        tools=(RecordingTool(),),
    )

    response = await router.handle(request())

    assert response.text == "Non ho quello strumento."
    result = model.calls[1][2]["content"][0]
    assert result["is_error"] is True
    assert "unknown tool: ghost" in result["content"]


async def test_failing_tool_is_reported_to_the_model_not_raised():
    router, model, _ = build_router(
        [tool_reply("broken"), text_reply("Lo strumento non ha funzionato.")],
        tools=(ExplodingTool(),),
    )

    response = await router.handle(request())

    assert response.text == "Lo strumento non ha funzionato."
    result = model.calls[1][2]["content"][0]
    assert result["is_error"] is True
    assert "boom" in result["content"]


async def test_tool_loop_stops_at_the_ceiling():
    tool = RecordingTool()
    router, _, memory = build_router(
        [tool_reply("clock", call_id=f"call-{i}") for i in range(3)],
        tools=(tool,),
        max_tool_iterations=3,
    )

    response = await router.handle(request())

    assert response.text == FALLBACK_TOO_MANY_STEPS
    assert response.degraded is True
    assert len(tool.calls) == 3
    assert await memory.get_history("chat-1") == []


async def test_model_unavailable_yields_a_polite_answer_and_stores_nothing():
    router, _, memory = build_router([LLMUnavailableError("no route to host")])

    response = await router.handle(request())

    assert response.text == FALLBACK_UNAVAILABLE
    assert response.degraded is True
    assert await memory.get_history("chat-1") == []


async def test_empty_model_answer_yields_a_polite_answer():
    router, _, memory = build_router([LLMResponse(blocks=(), stop_reason="end_turn")])

    response = await router.handle(request())

    assert response.text == FALLBACK_EMPTY
    assert response.degraded is True
    assert await memory.get_history("chat-1") == []


async def test_a_failed_turn_does_not_break_the_next_one():
    router, model, memory = build_router([LLMUnavailableError("offline"), text_reply("Eccomi.")])

    await router.handle(request("uno"))
    response = await router.handle(request("due"))

    assert response.text == "Eccomi."
    # The failed turn left no trace, so the model sees a clean history.
    assert model.calls[1] == [{"role": "user", "content": "due"}]
    assert len(await memory.get_history("chat-1")) == 2


def test_duplicate_tool_names_are_rejected():
    with pytest.raises(ValueError, match="duplicate tool name"):
        Router(
            llm=ScriptedModel([]),
            memory=InMemoryConversationMemory(max_messages=4),
            system_prompt=SYSTEM_PROMPT,
            tools=(RecordingTool(), RecordingTool()),
        )
