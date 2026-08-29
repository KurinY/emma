"""Tests for the in-memory conversation store."""

from __future__ import annotations

import pytest

from core.memory import InMemoryConversationMemory, StoredMessage


def user(text: str) -> StoredMessage:
    """Build a user message."""
    return StoredMessage(role="user", content=text)


def assistant(text: str) -> StoredMessage:
    """Build an assistant message."""
    return StoredMessage(role="assistant", content=text)


async def test_unknown_conversation_is_empty():
    memory = InMemoryConversationMemory(max_messages=4)
    assert await memory.get_history("nobody") == []


async def test_append_then_read_preserves_order():
    memory = InMemoryConversationMemory(max_messages=10)
    await memory.append("c1", user("ciao"))
    await memory.append("c1", assistant("ciao a te"))

    assert await memory.get_history("c1") == [user("ciao"), assistant("ciao a te")]


async def test_conversations_are_isolated():
    memory = InMemoryConversationMemory(max_messages=10)
    await memory.append("c1", user("primo"))
    await memory.append("c2", user("secondo"))

    assert await memory.get_history("c1") == [user("primo")]
    assert await memory.get_history("c2") == [user("secondo")]


async def test_returned_history_is_a_copy():
    memory = InMemoryConversationMemory(max_messages=10)
    await memory.append("c1", user("ciao"))

    history = await memory.get_history("c1")
    history.append(assistant("mutazione esterna"))

    assert await memory.get_history("c1") == [user("ciao")]


async def test_window_drops_the_oldest_messages():
    memory = InMemoryConversationMemory(max_messages=4)
    for index in range(4):
        await memory.append("c1", user(f"u{index}"))
        await memory.append("c1", assistant(f"a{index}"))

    history = await memory.get_history("c1")
    assert len(history) <= 4
    assert history == [user("u2"), assistant("a2"), user("u3"), assistant("a3")]


async def test_window_never_starts_on_an_assistant_message():
    # A window of 3 over u/a/u/a would start on an assistant message, which the
    # Messages API rejects: the store drops one more instead.
    memory = InMemoryConversationMemory(max_messages=3)
    await memory.append("c1", user("u0"))
    await memory.append("c1", assistant("a0"))
    await memory.append("c1", user("u1"))
    await memory.append("c1", assistant("a1"))

    history = await memory.get_history("c1")
    assert history[0].role == "user"
    assert history == [user("u1"), assistant("a1")]


async def test_prune_is_idempotent():
    memory = InMemoryConversationMemory(max_messages=2)
    await memory.append("c1", user("u0"))
    await memory.append("c1", assistant("a0"))

    await memory.prune("c1")
    first = await memory.get_history("c1")
    await memory.prune("c1")

    assert await memory.get_history("c1") == first


async def test_prune_on_unknown_conversation_is_a_no_op():
    memory = InMemoryConversationMemory(max_messages=2)
    await memory.prune("nobody")
    assert await memory.get_history("nobody") == []


def test_window_size_must_be_positive():
    with pytest.raises(ValueError, match="greater than zero"):
        InMemoryConversationMemory(max_messages=0)
