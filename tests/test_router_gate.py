"""Tests for the router's half of switching a tool off.

The store and the two tools were covered from the start; this half was not, and
it is the half that carries two of the four requirements. Everything below was
demonstrated once by hand in a throwaway script, which is exactly the kind of
evidence that disappears — the reviewer was right that only tests keep it.

Two properties matter here and they are not the same. Hiding the declaration is
what usually stops a tool being used: a model cannot call what it cannot see.
Refusing at execution is what makes it a guarantee, because a call can already
be in flight from a round where the tool was still offered.
"""

from __future__ import annotations

from typing import ClassVar
from unittest.mock import AsyncMock

import pytest

from core.llm import LLMResponse, TextBlock, ToolUseBlock
from core.router import AssistantRequest, Router, Tool, ToolGate


class Gate:
    """A gate whose answer the test controls, and can change mid-turn."""

    def __init__(self, *names: str) -> None:
        self.off = set(names)
        self.reads = 0

    async def disabled(self) -> frozenset[str]:
        """Answer, and record having been asked."""
        self.reads += 1
        return frozenset(self.off)


class Noisy:
    """A gate that will not answer."""

    async def disabled(self) -> frozenset[str]:
        """Refuse to answer."""
        raise RuntimeError("database sparito")


class Counter:
    """A tool that records having run."""

    description = "does a thing"
    input_schema: ClassVar[dict] = {"type": "object", "properties": {}}

    def __init__(self, name: str) -> None:
        self.name = name
        self.runs = 0

    async def run(self, arguments: dict) -> str:
        """Record the call."""
        self.runs += 1
        return "done"


def text(body: str = "ok") -> LLMResponse:
    return LLMResponse(blocks=(TextBlock(text=body),), stop_reason="end_turn")


def calls(*names: str) -> LLMResponse:
    return LLMResponse(
        blocks=tuple(ToolUseBlock(id=f"c{i}", name=n, input={}) for i, n in enumerate(names)),
        stop_reason="tool_use",
    )


def build(*replies: LLMResponse, gate=None, tools=None) -> tuple[Router, AsyncMock]:
    llm = AsyncMock()
    llm.complete = AsyncMock(side_effect=list(replies))
    memory = AsyncMock()
    memory.get_history = AsyncMock(return_value=[])
    router = Router(
        llm=llm,
        memory=memory,
        system_prompt="Sei EMMA.",
        tools=tools if tools is not None else (Counter("alpha"), Counter("beta")),
        tool_gate=gate,
    )
    return router, llm


async def ask(router: Router) -> object:
    return await router.handle(AssistantRequest(conversation_id="1", user_id="1", text="ciao"))


def offered(llm: AsyncMock, call: int = 0) -> list[str]:
    """The tool names sent to the model on a given call."""
    schemas = llm.complete.call_args_list[call].kwargs["tools"]
    return [] if schemas is None else [s["name"] for s in schemas]


# --------------------------------------------------------------------------- #
# What the model is shown
# --------------------------------------------------------------------------- #


def test_a_gate_is_optional():
    """Every router built before this feature passed none, and still works."""
    router, _ = build(text())

    assert router.tool_gate is None


async def test_without_a_gate_everything_is_offered():
    router, llm = build(text())

    await ask(router)

    assert offered(llm) == ["alpha", "beta"]


async def test_a_switched_off_tool_is_not_offered():
    router, llm = build(text(), gate=Gate("alpha"))

    await ask(router)

    assert offered(llm) == ["beta"]


async def test_switching_everything_off_sends_no_tools_rather_than_an_empty_list():
    """An empty list is not the same thing as "no tools" to every provider."""
    router, llm = build(text(), gate=Gate("alpha", "beta"))

    await ask(router)

    assert llm.complete.call_args.kwargs["tools"] is None


async def test_a_gate_that_will_not_answer_offers_everything():
    """The safer of the two wrong directions, and the one that keeps her useful."""
    router, llm = build(text(), gate=Noisy())

    answer = await ask(router)

    assert offered(llm) == ["alpha", "beta"]
    assert not answer.degraded


async def test_a_failing_gate_is_reported(caplog):
    router, _ = build(text(), gate=Noisy())

    with caplog.at_level("ERROR"):
        await ask(router)

    assert any("switched off" in r.message for r in caplog.records)


# --------------------------------------------------------------------------- #
# What happens if it is called anyway
# --------------------------------------------------------------------------- #


async def test_a_switched_off_tool_is_not_run_even_when_asked_for():
    """A call can be in flight from a round where the tool was still offered."""
    alpha = Counter("alpha")
    router, _ = build(calls("alpha"), text(), gate=Gate("alpha"), tools=(alpha,))

    await ask(router)

    assert alpha.runs == 0


async def test_the_refusal_is_answered_rather_than_ignored():
    """The model needs something to say; silence would end the turn oddly."""
    router, llm = build(calls("alpha"), text(), gate=Gate("alpha"), tools=(Counter("alpha"),))

    await ask(router)

    results = llm.complete.call_args.kwargs["messages"][-1]["content"]
    assert "disattivato" in results[0]["content"]
    assert results[0]["is_error"]


async def test_a_tool_that_is_on_still_runs():
    """The guard must not have been bought by breaking the ordinary path."""
    beta = Counter("beta")
    router, _ = build(calls("beta"), text(), gate=Gate("alpha"), tools=(beta,))

    await ask(router)

    assert beta.runs == 1


# --------------------------------------------------------------------------- #
# The gate can change under the turn, because a tool changes it
# --------------------------------------------------------------------------- #


async def test_the_gate_is_not_read_once_and_cached():
    """Read once per turn it was stale, and this feature is what made it so.

    Switching a tool off is itself done by a tool, so the state the turn
    depends on is state the turn can change. How many reads exactly is an
    implementation detail -- once per round for the declarations, once per tool
    call for the refusal -- so this asserts only that it is not cached for the
    turn. The two tests below pin the behaviour that actually matters.
    """
    gate = Gate()
    router, _ = build(calls("beta"), text(), gate=gate)

    await ask(router)

    assert gate.reads > 1


async def test_a_tool_switched_off_mid_turn_stops_being_offered():
    """The assistant saying "da adesso non lo uso piu" and then using it."""
    gate = Gate()
    alpha = Counter("alpha")
    router, llm = build(calls("beta"), text(), gate=gate, tools=(alpha, Counter("beta")))

    async def switch_off_during_the_turn(*args, **kwargs):
        gate.off.add("alpha")
        return calls("beta") if llm.complete.await_count == 1 else text()

    llm.complete.side_effect = switch_off_during_the_turn

    await ask(router)

    assert offered(llm, 0) == ["alpha", "beta"]
    assert offered(llm, 1) == ["beta"]


async def test_a_tool_switched_off_mid_turn_is_refused_in_the_same_turn():
    gate = Gate()
    alpha = Counter("alpha")
    router, llm = build(gate=gate, tools=(alpha,))

    async def switch_off_then_ask_for_it(*args, **kwargs):
        if llm.complete.await_count == 1:
            gate.off.add("alpha")
            return calls("alpha")
        return text()

    llm.complete.side_effect = switch_off_then_ask_for_it

    await ask(router)

    assert alpha.runs == 0


# --------------------------------------------------------------------------- #
# The protocol itself
# --------------------------------------------------------------------------- #


def test_the_gate_is_structural_not_inherited():
    """Like Tool and ContextProvider: nothing has to import anything to be one."""
    assert isinstance(Gate(), ToolGate)


def test_a_tool_is_still_a_tool():
    assert isinstance(Counter("alpha"), Tool)


@pytest.mark.parametrize("missing", [object(), None])
def test_something_without_the_method_is_not_a_gate(missing):
    assert not isinstance(missing, ToolGate)
