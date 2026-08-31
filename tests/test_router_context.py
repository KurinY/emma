"""Tests for context providers: facts put in front of the model every turn.

They exist because a tool is only consulted when the model chooses to consult
it, and measurement on the running assistant showed that choice going wrong
four times in ten when a stale answer sat in the conversation. What these tests
protect is the property that makes the difference: the line is present whether
or not anything decides to look.
"""

from __future__ import annotations

import pathlib
from typing import Any

import pytest

from core.llm import LLMResponse, Message, TextBlock, ToolUseBlock
from core.memory import InMemoryConversationMemory
from core.router import AssistantRequest, ContextProvider, Router
from core.tasks import TaskStore
from tools.development import DevelopmentContext

BASE_PROMPT = "Sei EMMA."


class RecordingModel:
    """A model that records the system prompt it was handed."""

    def __init__(self, replies: list[LLMResponse] | None = None) -> None:
        self._replies = list(
            replies or [LLMResponse(blocks=(TextBlock(text="ok"),), stop_reason="end_turn")]
        )
        self.systems: list[str] = []

    async def complete(
        self,
        *,
        system: str,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        """Record the prompt and return the next scripted reply."""
        self.systems.append(system)
        return self._replies.pop(0) if len(self._replies) > 1 else self._replies[0]


class Fixed:
    """A provider that always says the same thing."""

    def __init__(self, line: str) -> None:
        self.line = line
        self.calls = 0

    async def snapshot(self) -> str:
        """Return the fixed line and count the call."""
        self.calls += 1
        return self.line


class Broken:
    """A provider that fails, because one day one will."""

    async def snapshot(self) -> str:
        """Fail, the way a real provider eventually will."""
        raise RuntimeError("boom")


def build(model, providers=()) -> Router:
    return Router(
        llm=model,
        memory=InMemoryConversationMemory(max_messages=10),
        system_prompt=BASE_PROMPT,
        context_providers=providers,
    )


async def turn(router: Router, text: str = "ciao"):
    return await router.handle(AssistantRequest(text=text, user_id="u", conversation_id="c"))


# --------------------------------------------------------------------------- #
# The mechanism
# --------------------------------------------------------------------------- #


async def test_without_providers_the_prompt_is_untouched():
    model = RecordingModel()
    await turn(build(model))

    assert model.systems == [BASE_PROMPT]


async def test_a_provider_is_appended_to_the_prompt():
    model = RecordingModel()
    await turn(build(model, (Fixed("Lavori aperti: 2."),)))

    assert model.systems[0].startswith(BASE_PROMPT)
    assert "Lavori aperti: 2." in model.systems[0]


async def test_every_provider_contributes():
    model = RecordingModel()
    await turn(build(model, (Fixed("primo"), Fixed("secondo"))))

    assert "primo" in model.systems[0]
    assert "secondo" in model.systems[0]


async def test_a_provider_with_nothing_to_say_adds_nothing():
    model = RecordingModel()
    await turn(build(model, (Fixed(""),)))

    assert model.systems == [BASE_PROMPT]


async def test_a_broken_provider_does_not_cost_the_reply():
    """Answering without one line of context beats not answering."""
    model = RecordingModel()
    reply = await turn(build(model, (Broken(), Fixed("sopravvive"))))

    assert reply.degraded is False
    assert "sopravvive" in model.systems[0]


async def test_the_state_is_read_once_per_turn_not_once_per_tool_round():
    """It cannot change mid-turn, and it is paid for every time it is read."""
    model = RecordingModel(
        [
            LLMResponse(
                blocks=(ToolUseBlock(id="c1", name="assente", input={}),),
                stop_reason="tool_use",
            ),
            LLMResponse(blocks=(TextBlock(text="fatto"),), stop_reason="end_turn"),
        ]
    )
    provider = Fixed("una riga")
    await turn(build(model, (provider,)))

    assert len(model.systems) > 1, "the turn should have taken more than one round"
    assert provider.calls == 1


async def test_a_provider_satisfies_the_protocol():
    assert isinstance(Fixed("x"), ContextProvider)


# --------------------------------------------------------------------------- #
# The development queue provider
# --------------------------------------------------------------------------- #


@pytest.fixture
async def store(tmp_path: pathlib.Path):
    s = TaskStore(db_path=tmp_path / "ctx.db")
    await s.open()
    yield s
    await s.close()


async def test_an_empty_queue_says_so_rather_than_staying_silent(store):
    """Silence would let a stale 'there is one job' stand unchallenged."""
    line = await DevelopmentContext(store).snapshot()

    assert "nessuno aperto" in line


async def test_the_line_carries_the_count_and_the_numbers(store):
    first = await store.create("primo")
    second = await store.create("secondo")

    line = await DevelopmentContext(store).snapshot()

    assert "2 aperti" in line
    assert f"#{first}" in line and f"#{second}" in line


async def test_the_ones_waiting_on_the_user_are_singled_out(store):
    waiting = await store.create("in attesa")
    await store.create("non in attesa")
    await store.advance(waiting, "understood", "Procedo?")

    line = await DevelopmentContext(store).snapshot()

    assert "1 attendono una risposta" in line
    assert f"#{waiting}" in line


async def test_the_line_tells_the_model_which_source_wins(store):
    """The whole point: contradict the memory instead of hoping it is checked."""
    await store.create("un lavoro")

    line = await DevelopmentContext(store).snapshot()

    assert "sempre aggiornata" in line
    assert "vecchia" in line


async def test_finished_work_leaves_the_line(store):
    task_id = await store.create("finito")
    await store.finish(task_id)

    line = await DevelopmentContext(store).snapshot()

    assert "nessuno aperto" in line


async def test_the_development_provider_satisfies_the_protocol(store):
    assert isinstance(DevelopmentContext(store), ContextProvider)
