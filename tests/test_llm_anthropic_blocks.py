"""Tests for reading an Anthropic reply into this project's own block types.

The Groq dialect has a test file to itself, because translating between the two
is fiddly and got a whole release wrong once: the Groq client silently ignored
every tool declaration it was handed. The Anthropic side was translated by
`_to_response` and never tested at all -- the same asymmetry that let the first
bug through, pointing the other way.

Nothing here calls the network. `_to_response` reads whatever object the SDK
hands back, so the tests hand it the same shape.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.llm import TextBlock, ToolUseBlock, _to_response


def block(kind: str, **fields):
    return SimpleNamespace(type=kind, **fields)


def reply(*blocks, stop_reason: str = "end_turn"):
    return SimpleNamespace(content=list(blocks), stop_reason=stop_reason)


def test_prose_becomes_a_text_block():
    answer = _to_response(reply(block("text", text="Sono le 18:30.")))

    assert answer.blocks == (TextBlock(text="Sono le 18:30."),)
    assert answer.text == "Sono le 18:30."


def test_a_tool_call_becomes_a_tool_use_block():
    """Never exercised before, on the provider the project was built for."""
    answer = _to_response(
        reply(
            block("tool_use", id="call-1", name="work_status", input={"verbose": True}),
            stop_reason="tool_use",
        )
    )

    assert answer.tool_uses == (
        ToolUseBlock(id="call-1", name="work_status", input={"verbose": True}),
    )
    assert answer.stop_reason == "tool_use"


def test_prose_and_a_call_arrive_together():
    """A turn may explain itself and act in the same breath."""
    answer = _to_response(
        reply(
            block("text", text="Guardo la coda."),
            block("tool_use", id="c1", name="work_status", input={}),
            stop_reason="tool_use",
        )
    )

    assert answer.text == "Guardo la coda."
    assert len(answer.tool_uses) == 1


def test_several_calls_in_one_turn_are_all_kept():
    answer = _to_response(
        reply(
            block("tool_use", id="c1", name="work_status", input={}),
            block("tool_use", id="c2", name="running_version", input={}),
            stop_reason="tool_use",
        )
    )

    assert [call.id for call in answer.tool_uses] == ["c1", "c2"]


def test_arguments_that_are_not_an_object_do_not_lose_the_call():
    """A malformed input must not drop the call: the id still needs answering."""
    answer = _to_response(
        reply(block("tool_use", id="c1", name="work_status", input="oops"), stop_reason="tool_use")
    )

    assert answer.tool_uses[0].input == {}
    assert answer.tool_uses[0].id == "c1"


def test_an_unknown_block_type_is_skipped_not_fatal(caplog):
    """New block types get added to the API; an old client must not crash."""
    with caplog.at_level("DEBUG"):
        answer = _to_response(reply(block("text", text="ciao"), block("thinking", thinking="...")))

    assert answer.text == "ciao"
    assert len(answer.blocks) == 1


def test_a_reply_with_no_blocks_at_all_is_survived():
    answer = _to_response(reply())

    assert answer.blocks == ()
    assert answer.text == ""


@pytest.mark.parametrize("stop", ["end_turn", "max_tokens", "stop_sequence", "tool_use"])
def test_the_stop_reason_is_carried_through_unchanged(stop):
    """The router decides what to do with it; translating it would hide it."""
    assert _to_response(reply(block("text", text="x"), stop_reason=stop)).stop_reason == stop
