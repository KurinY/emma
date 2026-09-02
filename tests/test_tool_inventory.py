"""Tests for the tool EMMA uses to say what she can do.

Built because the user noticed something real: asked how many tools she had and
which, she could not say. Tool declarations reach the model through the API's
own field -- as functions available to call, not as data to read -- so
enumerating them is not something a model can reliably do about itself.

The inventory is given the same tuple the router gets, itself included. Any
list assembled by hand would be a second place to update and the first to be
forgotten, which is the failure these tests are mostly about.
"""

from __future__ import annotations

import pytest

from core.router import Tool
from tools.clock import clock_tools
from tools.introspection import ToolInventory, introspection_tools


@pytest.fixture
def inventory():
    """An inventory that describes a small, known set including itself."""
    inv = ToolInventory()
    tools = (*introspection_tools(), *clock_tools(), inv)
    inv.describes(tools)
    return inv


def test_it_satisfies_the_protocol(inventory):
    assert isinstance(inventory, Tool)
    assert inventory.name == "list_tools"


async def test_it_reports_how_many_and_which(inventory):
    answer = await inventory.run({})

    assert "3 strumenti" in answer
    assert "running_version" in answer
    assert "current_time" in answer


async def test_it_counts_itself(inventory):
    """It is a tool she has; leaving it out would be a smaller lie than most."""
    assert "list_tools" in await inventory.run({})


async def test_the_short_form_leaves_the_descriptions_out(inventory):
    """Asked how many, the answer should not be three paragraphs."""
    answer = await inventory.run({})

    assert "Report which version" not in answer
    assert len(answer) < 200


async def test_the_detailed_form_says_what_each_is_for(inventory):
    answer = await inventory.run({"detailed": True})

    assert "Report which version" in answer
    assert "current date and time" in answer


@pytest.mark.parametrize("falsy", [{}, {"detailed": False}, {"detailed": None}])
async def test_anything_but_true_gives_the_short_form(inventory, falsy):
    assert "Report which version" not in await inventory.run(falsy)


async def test_one_tool_is_not_reported_as_one_strumenti(inventory):
    """The plural nobody notices until a user does."""
    inv = ToolInventory()
    inv.describes(clock_tools())

    assert "1 strumento:" in await inv.run({})


async def test_an_inventory_nobody_filled_in_says_so(inventory):
    """A wiring mistake, and it must not be reported as "no tools"."""
    answer = await ToolInventory().run({})

    assert "Non lo so" in answer


async def test_it_describes_whatever_it_was_given(inventory):
    """The point of the two-phase wiring: no hand-written list to drift."""
    inv = ToolInventory()
    inv.describes((*clock_tools(), *introspection_tools()))

    answer = await inv.run({})

    assert "2 strumenti" in answer


def test_the_description_forbids_answering_from_memory(inventory):
    """The reason it exists: what the model assumes about itself is not evidence."""
    assert "from memory" in inventory.description


def test_the_description_asks_for_italian(inventory):
    """The descriptions are written for the model, in English."""
    assert "Italian" in inventory.description
