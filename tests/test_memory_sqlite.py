"""Tests for the SQLite-backed conversation store.

Each test gets its own temporary database file (via tmp_path) so tests are
fully isolated and leave no state behind.
"""

from __future__ import annotations

import os

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


# --------------------------------------------------------------------------- #
# Integrity and recovery
#
# Corruption is simulated by overwriting the file with bytes that are not an
# SQLite database.  That is what a damaged file looks like to the integrity
# check, which is the only thing the recovery path reacts to.
# --------------------------------------------------------------------------- #


def corrupt(path):
    """Overwrite a database file with something SQLite cannot read."""
    path.write_bytes(b"this is not an sqlite database" * 40)


async def test_snapshot_is_written_on_open(tmp_path):
    db = tmp_path / "snap.db"

    m = SqliteConversationMemory(db_path=db, max_messages=10)
    await m.open()
    await m.close()

    assert (tmp_path / "snap.db.snapshot").exists()


async def test_healthy_database_is_left_alone(tmp_path):
    db = tmp_path / "fine.db"

    m1 = SqliteConversationMemory(db_path=db, max_messages=10)
    await m1.open()
    await m1.append("c1", user("intatto"))
    await m1.close()

    m2 = SqliteConversationMemory(db_path=db, max_messages=10)
    await m2.open()
    history = await m2.get_history("c1")
    await m2.close()

    assert history == [user("intatto")]
    # Nothing was quarantined: a healthy file must never trigger recovery.
    assert list(tmp_path.glob("*.corrupt-*")) == []


async def test_corrupt_database_is_recovered_from_the_snapshot(tmp_path):
    db = tmp_path / "broken.db"

    m1 = SqliteConversationMemory(db_path=db, max_messages=10)
    await m1.open()
    await m1.append("c1", user("da salvare"))
    await m1.close()  # writes a snapshot containing that message

    corrupt(db)

    m2 = SqliteConversationMemory(db_path=db, max_messages=10)
    await m2.open()
    history = await m2.get_history("c1")
    await m2.close()

    assert history == [user("da salvare")]


async def test_corrupt_database_is_quarantined_not_deleted(tmp_path):
    db = tmp_path / "keepme.db"

    m1 = SqliteConversationMemory(db_path=db, max_messages=10)
    await m1.open()
    await m1.close()

    corrupt(db)
    damaged_bytes = db.read_bytes()

    m2 = SqliteConversationMemory(db_path=db, max_messages=10)
    await m2.open()
    await m2.close()

    quarantined = list(tmp_path.glob("keepme.db.corrupt-*"))
    assert len(quarantined) == 1
    assert quarantined[0].read_bytes() == damaged_bytes


async def test_recovery_falls_back_to_the_previous_snapshot(tmp_path):
    db = tmp_path / "fallback.db"

    m1 = SqliteConversationMemory(db_path=db, max_messages=10)
    await m1.open()
    await m1.append("c1", user("nella generazione vecchia"))
    await m1.close()

    # A second cycle rotates that snapshot into .prev.
    m2 = SqliteConversationMemory(db_path=db, max_messages=10)
    await m2.open()
    await m2.close()

    # The newest snapshot is unusable, the one behind it is not.
    corrupt(tmp_path / "fallback.db.snapshot")
    corrupt(db)

    m3 = SqliteConversationMemory(db_path=db, max_messages=10)
    await m3.open()
    history = await m3.get_history("c1")
    await m3.close()

    assert history == [user("nella generazione vecchia")]


async def test_corrupt_database_without_any_snapshot_starts_empty(tmp_path):
    db = tmp_path / "hopeless.db"

    m1 = SqliteConversationMemory(db_path=db, max_messages=10)
    await m1.open()
    await m1.append("c1", user("perduto"))
    await m1.close()

    corrupt(db)
    for leftover in tmp_path.glob("hopeless.db.snapshot*"):
        leftover.unlink()

    m2 = SqliteConversationMemory(db_path=db, max_messages=10)
    await m2.open()
    history = await m2.get_history("c1")
    # Starting empty is a valid outcome, but the store must still work.
    await m2.append("c1", user("ricomincio"))
    after = await m2.get_history("c1")
    await m2.close()

    assert history == []
    assert after == [user("ricomincio")]


async def test_recovery_is_not_disturbed_by_a_stale_wal(tmp_path):
    """A write-ahead log left behind by the damaged file must not be replayed.

    Whether the stale companion is quarantined by us or discarded by SQLite
    when it fails to open the broken file does not matter; what matters is
    that it never lands on top of the restored database.
    """
    db = tmp_path / "wal.db"

    m1 = SqliteConversationMemory(db_path=db, max_messages=10)
    await m1.open()
    await m1.append("c1", user("prima"))
    await m1.close()

    corrupt(db)
    (tmp_path / "wal.db-wal").write_bytes(b"stale write ahead log")

    m2 = SqliteConversationMemory(db_path=db, max_messages=10)
    await m2.open()
    history = await m2.get_history("c1")
    await m2.append("c1", assistant("dopo il recupero"))
    after = await m2.get_history("c1")
    await m2.close()

    assert history == [user("prima")]
    assert after == [user("prima"), assistant("dopo il recupero")]


async def test_snapshot_is_not_world_readable(tmp_path):
    """A snapshot holds the same conversations as the database itself."""
    db = tmp_path / "perms.db"

    m = SqliteConversationMemory(db_path=db, max_messages=10)
    await m.open()
    await m.append("c1", user("riservato"))
    await m.close()

    mode = (tmp_path / "perms.db.snapshot").stat().st_mode & 0o777
    # Windows does not implement POSIX permission bits; the assertion that
    # matters is the one that runs on the platform EMMA is deployed to.
    if os.name == "posix":
        assert mode == 0o600, f"expected 0600, got {mode:o}"


async def test_write_ahead_logging_is_enabled(tmp_path):
    db = tmp_path / "mode.db"

    m = SqliteConversationMemory(db_path=db, max_messages=10)
    await m.open()
    cursor = await m._db.execute("PRAGMA journal_mode")
    (mode,) = await cursor.fetchone()
    await m.close()

    assert mode.lower() == "wal"
