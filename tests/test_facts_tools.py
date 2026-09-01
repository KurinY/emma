"""Tests for the two tools and the context provider.

What the model may do here is deliberately narrow: write a fact when asked,
stop using one when asked. It may not decide by itself what is worth keeping --
that is the same class of risk as an automatic summary, where a plausible wrong
answer is the one nobody thinks to check (REVISIONE.md, entries 17.10 and 18).

The provider is the part that costs money. Everything active goes in front of
the model on every turn, so the tests below are as much about what it refuses
to send as about what it sends.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from core.router import Tool
from tools.facts.store import MAX_ACTIVE_FACTS, MAX_FACT_LENGTH, FactStore
from tools.facts.tools import MAX_CONTEXT_CHARS, FactsContext, facts_tools


@pytest.fixture
async def store(tmp_path):
    s = FactStore(db_path=tmp_path / "facts.db")
    await s.open()
    yield s
    await s.close()


@pytest.fixture
def tools(store):
    return facts_tools(store)


# --------------------------------------------------------------------------- #
# The contract with the router
# --------------------------------------------------------------------------- #


def test_both_tools_satisfy_the_protocol(tools):
    """Structural, not inherited: registering a tool touches no router code."""
    for tool in tools:
        assert isinstance(tool, Tool)


def test_there_are_exactly_two_of_them(tools):
    """No `recall`, on purpose.

    Everything active is already in front of the model, so a third declaration
    would be paid for on every turn to answer a question it can already see the
    answer to.
    """
    assert [t.name for t in tools] == ["remember_fact", "forget_fact"]


def test_the_descriptions_say_the_user_must_ask(tools):
    """The narrowness is the design; it has to reach the model to hold."""
    remember = tools[0]

    assert "explicitly" in remember.description
    assert "never on" in remember.description


# --------------------------------------------------------------------------- #
# Remembering
# --------------------------------------------------------------------------- #


async def test_a_fact_is_recorded_and_numbered(tools, store):
    reply = await tools[0].run({"fact": "mia figlia si chiama Sara"})

    assert "#1" in reply
    assert len(await store.active()) == 1


async def test_the_reply_promises_what_the_feature_is_for(tools):
    """The user's question was "after twenty prompts does she forget?"."""
    reply = await tools[0].run({"fact": "abito a Modena"})

    assert "giorni" in reply


async def test_a_duplicate_says_so_without_storing_it_twice(tools, store):
    await tools[0].run({"fact": "il gatto si chiama Nero"})

    reply = await tools[0].run({"fact": "il gatto si chiama Nero"})

    assert "gia'" in reply
    assert len(await store.active()) == 1


async def test_an_empty_fact_is_refused_with_a_reason(tools, store):
    reply = await tools[0].run({"fact": "   "})

    assert "Non l'ho registrato" in reply
    assert await store.active() == []


async def test_a_missing_argument_records_nothing(tools, store):
    reply = await tools[0].run({})

    assert "Non l'ho registrato" in reply
    assert await store.active() == []


async def test_an_over_long_fact_is_refused_with_the_limit(tools):
    reply = await tools[0].run({"fact": "x" * (MAX_FACT_LENGTH + 50)})

    assert str(MAX_FACT_LENGTH) in reply


async def test_the_ceiling_is_reported_in_words_the_user_can_act_on(tools):
    for i in range(MAX_ACTIVE_FACTS):
        await tools[0].run({"fact": f"fatto {i}"})

    reply = await tools[0].run({"fact": "uno di troppo"})

    assert str(MAX_ACTIVE_FACTS) in reply
    assert "dimentica" in reply


# --------------------------------------------------------------------------- #
# Forgetting
# --------------------------------------------------------------------------- #


async def test_forgetting_takes_it_out_of_use(tools, store):
    await tools[0].run({"fact": "una cosa"})

    reply = await tools[1].run({"number": 1})

    assert "#1" in reply
    assert await store.active() == []


async def test_forgetting_something_absent_changes_nothing(tools):
    reply = await tools[1].run({"number": 999})

    assert "non esiste" in reply


@pytest.mark.parametrize("bad", [{}, {"number": "il primo"}, {"number": None}])
async def test_a_bad_number_forgets_nothing(tools, store, bad):
    await tools[0].run({"fact": "da non perdere"})

    reply = await tools[1].run(bad)

    assert "non valido" in reply
    assert len(await store.active()) == 1


# --------------------------------------------------------------------------- #
# What reaches the model, and what it costs
# --------------------------------------------------------------------------- #


async def test_nothing_remembered_means_nothing_injected(store):
    """An empty provider must cost zero tokens, not a header saying it is empty."""
    assert await FactsContext(store).snapshot() == ""


async def test_the_facts_reach_the_model_with_their_numbers(store, tools):
    await tools[0].run({"fact": "mia figlia si chiama Sara"})
    await tools[0].run({"fact": "abito a Modena"})

    text = await FactsContext(store).snapshot()

    assert "#1: mia figlia si chiama Sara" in text
    assert "#2: abito a Modena" in text


async def test_a_forgotten_fact_stops_reaching_the_model(store, tools):
    await tools[0].run({"fact": "una cosa da dimenticare"})
    await tools[1].run({"number": 1})

    assert await FactsContext(store).snapshot() == ""


async def test_the_facts_are_declared_to_outrank_the_recent_conversation(store, tools):
    """The lesson of the poisoned window: state that changes needs an order."""
    await tools[0].run({"fact": "qualcosa"})

    text = await FactsContext(store).snapshot()

    assert "valgono piu' della conversazione recente" in text


async def test_the_context_is_capped_however_many_facts_there_are(store):
    for i in range(MAX_ACTIVE_FACTS):
        await store.remember(f"fatto lungo numero {i} " + "x" * 200)

    text = await FactsContext(store).snapshot()

    assert len(text) < MAX_CONTEXT_CHARS + 500  # header and the note about the rest


async def test_facts_left_out_are_admitted_rather_than_dropped_silently(store, caplog):
    for i in range(MAX_ACTIVE_FACTS):
        await store.remember(f"fatto {i} " + "y" * 200)

    with caplog.at_level("WARNING"):
        text = await FactsContext(store).snapshot()

    assert "non entrano" in text
    assert any("left out of the context" in r.message for r in caplog.records)


async def test_a_broken_provider_does_not_cost_the_turn(store):
    """The router logs it and answers anyway; this asserts the router's part."""
    from core.router import AssistantRequest, Router

    broken = FactsContext(store)
    broken.snapshot = AsyncMock(side_effect=RuntimeError("database sparito"))

    llm = AsyncMock()
    from core.llm import LLMResponse, TextBlock

    llm.complete = AsyncMock(
        return_value=LLMResponse(blocks=(TextBlock(text="ok"),), stop_reason="end_turn")
    )
    memory = AsyncMock()
    memory.get_history = AsyncMock(return_value=[])
    router = Router(llm=llm, memory=memory, system_prompt="Sei EMMA.", context_providers=(broken,))

    answer = await router.handle(AssistantRequest(conversation_id="1", user_id="1", text="ciao"))

    assert answer.text == "ok"
    assert not answer.degraded
