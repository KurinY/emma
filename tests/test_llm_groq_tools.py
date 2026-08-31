"""Tests for the Anthropic-to-Groq translation of tool traffic.

The router speaks one dialect and Groq another, and the whole of the difference
lives in three functions. They are tested directly rather than through the
client, because what can go wrong here is the shape of a dictionary, and a test
that asserts on the shape says so plainly when it breaks.

The case that matters most is the second round of an agentic turn: the history
replayed to the model has to still contain the call it made and the result it
got back. Flattening that to prose -- which this adapter used to do -- leaves
the model unable to see it ever called anything.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from core.llm import (
    TextBlock,
    ToolUseBlock,
    _from_groq_message,
    _to_groq_messages,
    _to_groq_tools,
)

SYSTEM = "Sei un assistente."


def fake_call(call_id: str, name: str, arguments: str) -> SimpleNamespace:
    """Build the shape the Groq SDK returns for one tool call."""
    return SimpleNamespace(
        id=call_id,
        type="function",
        function=SimpleNamespace(name=name, arguments=arguments),
    )


def fake_message(content=None, tool_calls=None) -> SimpleNamespace:
    return SimpleNamespace(content=content, tool_calls=tool_calls)


# --------------------------------------------------------------------------- #
# Declarations
# --------------------------------------------------------------------------- #


def test_declarations_are_nested_under_function():
    groq = _to_groq_tools(
        [
            {
                "name": "work_status",
                "description": "Elenca i lavori.",
                "input_schema": {"type": "object", "properties": {}},
            }
        ]
    )

    assert groq == [
        {
            "type": "function",
            "function": {
                "name": "work_status",
                "description": "Elenca i lavori.",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]


def test_a_declaration_without_a_schema_still_gets_one():
    """Groq rejects a function with no parameters object at all."""
    (groq,) = _to_groq_tools([{"name": "t", "description": "d", "input_schema": None}])
    assert groq["function"]["parameters"] == {"type": "object", "properties": {}}


def test_every_declaration_is_translated():
    tools = [{"name": f"t{i}", "description": "d", "input_schema": {}} for i in range(3)]
    assert len(_to_groq_tools(tools)) == 3


# --------------------------------------------------------------------------- #
# Outbound history
# --------------------------------------------------------------------------- #


def test_the_system_prompt_comes_first():
    out = _to_groq_messages(SYSTEM, [])
    assert out == [{"role": "system", "content": SYSTEM}]


def test_plain_messages_pass_through():
    out = _to_groq_messages(SYSTEM, [{"role": "user", "content": "ciao"}])
    assert out[1] == {"role": "user", "content": "ciao"}


def test_an_assistant_turn_of_pure_prose():
    out = _to_groq_messages(
        SYSTEM,
        [{"role": "assistant", "content": [{"type": "text", "text": "ecco"}]}],
    )
    assert out[1] == {"role": "assistant", "content": "ecco"}


def test_a_tool_call_becomes_a_field_not_a_block():
    out = _to_groq_messages(
        SYSTEM,
        [
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "call_1",
                        "name": "work_status",
                        "input": {"numero": 3},
                    }
                ],
            }
        ],
    )

    assistant = out[1]
    assert assistant["role"] == "assistant"
    # No prose alongside the call: the API wants content explicitly null.
    assert assistant["content"] is None
    (call,) = assistant["tool_calls"]
    assert call["id"] == "call_1"
    assert call["type"] == "function"
    assert call["function"]["name"] == "work_status"
    # Arguments travel as a JSON string, not as an object.
    assert isinstance(call["function"]["arguments"], str)
    assert json.loads(call["function"]["arguments"]) == {"numero": 3}


def test_prose_and_a_call_in_the_same_turn():
    out = _to_groq_messages(
        SYSTEM,
        [
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "guardo"},
                    {"type": "tool_use", "id": "c1", "name": "work_status", "input": {}},
                ],
            }
        ],
    )

    assert out[1]["content"] == "guardo"
    assert len(out[1]["tool_calls"]) == 1


def test_a_call_with_no_arguments_serialises_to_an_empty_object():
    out = _to_groq_messages(
        SYSTEM,
        [
            {
                "role": "assistant",
                "content": [{"type": "tool_use", "id": "c1", "name": "t", "input": None}],
            }
        ],
    )
    assert out[1]["tool_calls"][0]["function"]["arguments"] == "{}"


def test_a_result_becomes_a_message_of_its_own():
    out = _to_groq_messages(
        SYSTEM,
        [
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "c1", "content": "nessun lavoro"}
                ],
            }
        ],
    )

    assert out[1] == {"role": "tool", "tool_call_id": "c1", "content": "nessun lavoro"}


def test_several_results_become_several_messages():
    out = _to_groq_messages(
        SYSTEM,
        [
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "c1", "content": "a"},
                    {"type": "tool_result", "tool_use_id": "c2", "content": "b"},
                ],
            }
        ],
    )

    assert [m["tool_call_id"] for m in out[1:]] == ["c1", "c2"]


def test_a_failed_tool_still_reports_back():
    """Dropping the result would leave the call unanswered and the API unhappy."""
    out = _to_groq_messages(
        SYSTEM,
        [
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "c1",
                        "content": "tool error: boom",
                        "is_error": True,
                    }
                ],
            }
        ],
    )

    assert out[1]["content"] == "tool error: boom"


def test_a_whole_agentic_round_trip_keeps_its_shape():
    """The ordering the API requires: call, then its result, then the answer."""
    history = [
        {"role": "user", "content": "a che punto sono i lavori?"},
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "c1", "name": "work_status", "input": {}}],
        },
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "c1", "content": "#1 aperto"}],
        },
    ]

    out = _to_groq_messages(SYSTEM, history)

    assert [m["role"] for m in out] == ["system", "user", "assistant", "tool"]
    assert out[2]["tool_calls"][0]["id"] == "c1"
    assert out[3]["tool_call_id"] == "c1"


def test_text_alongside_a_result_survives_as_a_user_message():
    out = _to_groq_messages(
        SYSTEM,
        [
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "c1", "content": "fatto"},
                    {"type": "text", "text": "e poi?"},
                ],
            }
        ],
    )

    assert [m["role"] for m in out] == ["system", "tool", "user"]
    assert out[2]["content"] == "e poi?"


# --------------------------------------------------------------------------- #
# Inbound reply
# --------------------------------------------------------------------------- #


def test_prose_becomes_a_text_block():
    reply = _from_groq_message(fake_message(content="ciao"), "stop")

    assert reply.blocks == (TextBlock(text="ciao"),)
    assert reply.stop_reason == "stop"
    assert reply.tool_uses == ()


def test_a_tool_call_becomes_a_tool_use_block():
    reply = _from_groq_message(
        fake_message(tool_calls=[fake_call("c1", "work_status", '{"numero": 3}')]),
        "tool_calls",
    )

    assert reply.tool_uses == (ToolUseBlock(id="c1", name="work_status", input={"numero": 3}),)


def test_the_stop_reason_is_translated_for_the_router():
    """The loop keys on 'tool_use'; Groq says 'tool_calls'."""
    reply = _from_groq_message(fake_message(tool_calls=[fake_call("c1", "t", "{}")]), "tool_calls")
    assert reply.stop_reason == "tool_use"


def test_other_stop_reasons_are_left_alone():
    assert _from_groq_message(fake_message(content="x"), "length").stop_reason == "length"


def test_prose_and_a_call_arrive_together():
    reply = _from_groq_message(
        fake_message(content="guardo", tool_calls=[fake_call("c1", "t", "{}")]),
        "tool_calls",
    )

    assert reply.text == "guardo"
    assert len(reply.tool_uses) == 1


def test_malformed_arguments_do_not_lose_the_call():
    """A dropped call would look like the model said nothing at all."""
    reply = _from_groq_message(
        fake_message(tool_calls=[fake_call("c1", "t", "{not json")]), "tool_calls"
    )

    assert reply.tool_uses == (ToolUseBlock(id="c1", name="t", input={}),)


def test_arguments_that_are_not_an_object_are_ignored():
    reply = _from_groq_message(
        fake_message(tool_calls=[fake_call("c1", "t", "[1,2,3]")]), "tool_calls"
    )
    assert reply.tool_uses[0].input == {}


def test_an_empty_reply_still_produces_a_block():
    """The router expects blocks; it turns empty prose into its own fallback."""
    reply = _from_groq_message(fake_message(), "stop")

    assert reply.blocks == (TextBlock(text=""),)
    assert reply.text == ""


def test_several_calls_in_one_reply():
    reply = _from_groq_message(
        fake_message(tool_calls=[fake_call("c1", "a", "{}"), fake_call("c2", "b", '{"x": 1}')]),
        "tool_calls",
    )

    assert [t.name for t in reply.tool_uses] == ["a", "b"]
    assert reply.tool_uses[1].input == {"x": 1}


def test_the_reply_can_be_replayed_back_out():
    """What comes in must be able to go out again: that is the agentic loop."""
    reply = _from_groq_message(
        fake_message(content="guardo", tool_calls=[fake_call("c1", "t", '{"n": 1}')]),
        "tool_calls",
    )

    out = _to_groq_messages(SYSTEM, [reply.to_assistant_message()])

    assert out[1]["content"] == "guardo"
    assert out[1]["tool_calls"][0]["id"] == "c1"
    assert json.loads(out[1]["tool_calls"][0]["function"]["arguments"]) == {"n": 1}
