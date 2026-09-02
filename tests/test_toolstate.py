"""Tests for switching a tool off, and for the second request that removes it.

The design is the user's (REVISIONE.md, entry 24) and the property worth
guarding is the one that makes it safer than a single switch: the irreversible
half never happens on a first request. "Already off" is not a formality, it is
evidence -- the tool has been gone a while and was not missed.

The other half of these tests is about locking yourself out. A tool able to
switch off `list_tools` or `enable_tool` would close the door and lose the key,
and the only way back would be an edit on the server.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from core.router import Tool, ToolGate
from core.tasks import TaskStore
from tools.clock import clock_tools
from tools.introspection import ToolInventory, introspection_tools
from tools.toolstate import PROTECTED, ToolStateStore, toolstate_tools


@pytest.fixture
async def wired(tmp_path):
    """The switch, the queue, and the tools that use them."""
    state = ToolStateStore(db_path=tmp_path / "s.db")
    tasks = TaskStore(db_path=tmp_path / "s.db")
    await state.open()
    await tasks.open()

    remove, enable = toolstate_tools(state, tasks)
    inventory = ToolInventory(gate=state)
    everything = (*introspection_tools(), *clock_tools(), remove, enable, inventory)
    for tool in everything:
        describes = getattr(tool, "describes", None)
        if describes is not None:
            describes(everything)

    yield state, tasks, remove, enable, inventory
    await tasks.close()
    await state.close()


# --------------------------------------------------------------------------- #
# The contract
# --------------------------------------------------------------------------- #


def test_both_tools_satisfy_the_protocol(wired):
    _, _, remove, enable, _ = wired

    assert isinstance(remove, Tool)
    assert isinstance(enable, Tool)


async def test_the_store_is_a_gate(wired):
    """The router asks it directly; nothing adapts between them."""
    state, *_ = wired

    assert isinstance(state, ToolGate)
    assert await state.disabled() == frozenset()


# --------------------------------------------------------------------------- #
# The first request switches off, and nothing else
# --------------------------------------------------------------------------- #


async def test_the_first_request_only_switches_off(wired):
    state, tasks, remove, _, _ = wired

    reply = await remove.run({"name": "current_time"})

    assert await state.disabled() == frozenset({"current_time"})
    assert await tasks.open_tasks() == []  # nothing irreversible yet
    assert "disattivato" in reply


async def test_the_first_reply_says_both_ways_out(wired):
    """The user has to know it is reversible, and how to go the other way."""
    _, _, remove, _, _ = wired

    reply = await remove.run({"name": "current_time"})

    assert "enable_tool" in reply
    assert "di nuovo" in reply


async def test_the_reason_is_kept_with_it(wired):
    state, _, remove, _, _ = wired

    await remove.run({"name": "current_time", "reason": "non lo uso mai"})

    (entry,) = await state.switched_off()
    assert entry.reason == "non lo uso mai"


# --------------------------------------------------------------------------- #
# The second request, and only then
# --------------------------------------------------------------------------- #


async def test_the_second_request_registers_the_removal(wired):
    state, tasks, remove, _, _ = wired
    await remove.run({"name": "current_time"})

    reply = await remove.run({"name": "current_time"})

    (job,) = await tasks.open_tasks()
    assert "current_time" in job.request
    assert f"#{job.id}" in reply
    assert await state.disabled() == frozenset({"current_time"})  # still off


async def test_the_second_request_carries_the_reason_into_the_job(wired):
    _, tasks, remove, _, _ = wired
    await remove.run({"name": "current_time"})

    await remove.run({"name": "current_time", "reason": "non serve a niente"})

    (job,) = await tasks.open_tasks()
    assert "non serve a niente" in job.request


async def test_switching_it_back_on_resets_the_two_stages(wired):
    """Re-enabling means the next removal starts from the beginning again."""
    state, tasks, remove, enable, _ = wired
    await remove.run({"name": "current_time"})
    await enable.run({"name": "current_time"})

    await remove.run({"name": "current_time"})

    assert await tasks.open_tasks() == []
    assert await state.disabled() == frozenset({"current_time"})


# --------------------------------------------------------------------------- #
# Not locking yourself out
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("protected", sorted(PROTECTED))
async def test_the_way_back_cannot_be_switched_off(wired, protected):
    state, _, remove, _, _ = wired

    reply = await remove.run({"name": protected})

    assert await state.disabled() == frozenset()
    assert "Non posso" in reply


def test_the_protected_set_names_tools_that_exist(wired):
    """A guard on a misspelled name protects nothing."""
    *_, inventory = wired

    assert {t.name for t in inventory._tools} >= PROTECTED


# --------------------------------------------------------------------------- #
# Names that are not names
# --------------------------------------------------------------------------- #


async def test_an_unknown_tool_is_refused_rather_than_recorded(wired):
    """Otherwise a typo would switch off something that does not exist."""
    state, _, remove, _, _ = wired

    reply = await remove.run({"name": "meteo"})

    assert await state.disabled() == frozenset()
    assert "list_tools" in reply


@pytest.mark.parametrize("bad", [{}, {"name": ""}, {"name": "   "}])
async def test_a_missing_name_does_nothing(wired, bad):
    state, _, remove, _, _ = wired

    reply = await remove.run(bad)

    assert await state.disabled() == frozenset()
    assert "mancante" in reply


async def test_enabling_something_that_was_not_off_changes_nothing(wired):
    _, _, _, enable, _ = wired

    reply = await enable.run({"name": "current_time"})

    assert "non era disattivato" in reply


async def test_enabling_without_a_name_does_nothing(wired):
    _, _, _, enable, _ = wired

    assert "mancante" in await enable.run({})


# --------------------------------------------------------------------------- #
# Surviving a restart
# --------------------------------------------------------------------------- #


async def test_a_switched_off_tool_stays_off_across_a_restart(tmp_path):
    """Otherwise the switch would be undone by the next deploy."""
    path = tmp_path / "s.db"
    first = ToolStateStore(db_path=path)
    await first.open()
    await first.disable("current_time", "per prova")
    await first.close()

    second = ToolStateStore(db_path=path)
    await second.open()
    try:
        assert await second.disabled() == frozenset({"current_time"})
    finally:
        await second.close()


async def test_switching_off_twice_is_not_two_entries(wired):
    state, _, _, _, _ = wired
    await state.disable("current_time")

    assert not await state.disable("current_time")
    assert len(await state.switched_off()) == 1


async def test_using_it_unopened_says_so(tmp_path):
    store = ToolStateStore(db_path=tmp_path / "s.db")

    with pytest.raises(RuntimeError, match="open"):
        await store.disabled()


# --------------------------------------------------------------------------- #
# What the user can see
# --------------------------------------------------------------------------- #


async def test_the_listing_marks_what_is_off(wired):
    """A tool merely missing from the list is one you cannot ask to bring back."""
    _, _, remove, _, inventory = wired
    await remove.run({"name": "current_time"})

    answer = await inventory.run({})

    assert "current_time (disattivato)" in answer
    assert "1 disattivati" in answer


async def test_the_listing_says_nothing_extra_when_nothing_is_off(wired):
    *_, inventory = wired

    assert "disattivat" not in await inventory.run({})


async def test_a_broken_gate_does_not_cost_the_listing(wired):
    """An inventory that says nothing is worse than one missing an annotation."""
    *_, inventory = wired
    inventory._gate = AsyncMock()
    inventory._gate.disabled = AsyncMock(side_effect=RuntimeError("database sparito"))

    assert "current_time" in await inventory.run({})
