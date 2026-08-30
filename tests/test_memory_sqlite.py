"""Tests for the SQLite-backed conversation store.

Each test gets its own temporary database file (via tmp_path) so tests are
fully isolated and leave no state behind.
"""

from __future__ import annotations

import pytest

from core.memory import SqliteConversationMemory, StoredMessage


def user(text: str) -> StoredMessage:
    return StoredMessage(role="user", content=text)


def assistant(text: str) -> StoredMessage:
    return StoredMessage(role="assistant", content=text)


@pytest.fixture
async def mem(tmp_path):
    m = SqliteConversationMemory(db_path=tmp_path / "test.db", max_messages=10)
    await m.open()
    yield m
    await m.close()


async def test_unknown_conversation_is_empty(mem):
    assert await mem.get_history("nobody") == []


async def test_append_then_read_preserves_order(mem):
    await mem.append("c1", user("ciao"))
    await mem.append("c1", assistant("ciao a te"))

    assert await mem.get_history("c1") == [user("ciao"), assistant("ciao a te")]


async def test_conversations_are_isolated(mem):
    await mem.append("c1", user("primo"))
    await mem.append("c2", user("secondo"))

    assert await mem.get_history("c1") == [user("primo")]
    assert await mem.get_history("c2") == [user("secondo")]


async def test_returned_history_is_a_copy(mem):
    await mem.append("c1", user("ciao"))

    history = await mem.get_history("c1")
    history.append(assistant("mutazione esterna"))

    assert await mem.get_history("c1") == [user("ciao")]


async def test_window_drops_the_oldest_messages(tmp_path):
    m = SqliteConversationMemory(db_path=tmp_path / "win.db", max_messages=4)
    await m.open()
    for i in range(4):
        await m.append("c1", user(f"u{i}"))
        await m.append("c1", assistant(f"a{i}"))

    history = await m.get_history("c1")
    assert len(history) <= 4
    assert history == [user("u2"), assistant("a2"), user("u3"), assistant("a3")]
    await m.close()


async def test_window_never_starts_on_an_assistant_message(tmp_path):
    m = SqliteConversationMemory(db_path=tmp_path / "role.db", max_messages=3)
    await m.open()
    await m.append("c1", user("u0"))
    await m.append("c1", assistant("a0"))
    await m.append("c1", user("u1"))
    await m.append("c1", assistant("a1"))

    history = await m.get_history("c1")
    assert history[0].role == "user"
    assert history == [user("u1"), assistant("a1")]
    await m.close()


async def test_prune_is_idempotent(tmp_path):
    m = SqliteConversationMemory(db_path=tmp_path / "prune.db", max_messages=2)
    await m.open()
    await m.append("c1", user("u0"))
    await m.append("c1", assistant("a0"))

    await m.prune("c1")
    first = await m.get_history("c1")
    await m.prune("c1")

    assert await m.get_history("c1") == first
    await m.close()


async def test_prune_on_unknown_conversation_is_a_no_op(mem):
    await mem.prune("nobody")
    assert await mem.get_history("nobody") == []


def test_window_size_must_be_positive(tmp_path):
    with pytest.raises(ValueError, match="greater than zero"):
        SqliteConversationMemory(db_path=tmp_path / "x.db", max_messages=0)


async def test_history_survives_reopen(tmp_path):
    db = tmp_path / "persist.db"

    m1 = SqliteConversationMemory(db_path=db, max_messages=10)
    await m1.open()
    await m1.append("c1", user("prima sessione"))
    await m1.close()

    m2 = SqliteConversationMemory(db_path=db, max_messages=10)
    await m2.open()
    history = await m2.get_history("c1")
    await m2.close()

    assert history == [user("prima sessione")]
