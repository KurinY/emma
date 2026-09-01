"""Tests for the fact store.

The store answers a question `core.memory` deliberately cannot: what should
survive being old. There, the criterion is recency and a `DELETE` enforces it;
here nothing expires, so every guard against unbounded growth has to be in this
file instead of in the schema.

The case that matters most is the last section: three stores now share one
SQLite file, and if that arrangement were fragile it would be fragile for the
conversation and the development queue too -- things that already work and must
keep working.
"""

from __future__ import annotations

import pathlib

import pytest

from core.memory import SqliteConversationMemory, StoredMessage
from core.tasks import TaskStore
from tools.facts.store import MAX_ACTIVE_FACTS, MAX_FACT_LENGTH, FactStore


@pytest.fixture
async def store(tmp_path):
    s = FactStore(db_path=tmp_path / "facts.db")
    await s.open()
    yield s
    await s.close()


# --------------------------------------------------------------------------- #
# Remembering
# --------------------------------------------------------------------------- #


async def test_a_fact_is_kept_and_numbered(store):
    fact_id, reason = await store.remember("mia figlia si chiama Sara")

    assert fact_id == 1
    assert reason == ""
    assert [f.text for f in await store.active()] == ["mia figlia si chiama Sara"]


async def test_facts_come_back_in_the_order_they_were_given(store):
    for text in ("primo", "secondo", "terzo"):
        await store.remember(text)

    assert [f.text for f in await store.active()] == ["primo", "secondo", "terzo"]


async def test_whitespace_is_normalised(store):
    """A fact pasted from elsewhere must not cost tokens for its formatting."""
    await store.remember("  la password   del wifi\n\ne' 1234  ")

    assert (await store.active())[0].text == "la password del wifi e' 1234"


@pytest.mark.parametrize("empty", ["", "   ", "\n\t "])
async def test_an_empty_fact_is_refused(store, empty):
    fact_id, reason = await store.remember(empty)

    assert fact_id is None
    assert "vuoto" in reason
    assert await store.active() == []


async def test_a_fact_longer_than_the_limit_is_refused(store):
    """A page pasted here would be paid for on every turn from now on."""
    fact_id, reason = await store.remember("x" * (MAX_FACT_LENGTH + 1))

    assert fact_id is None
    assert str(MAX_FACT_LENGTH) in reason
    assert await store.active() == []


async def test_a_fact_exactly_at_the_limit_is_accepted(store):
    fact_id, _ = await store.remember("x" * MAX_FACT_LENGTH)

    assert fact_id is not None


# --------------------------------------------------------------------------- #
# Not paying twice for the same thing
# --------------------------------------------------------------------------- #


async def test_the_same_fact_twice_is_stored_once(store):
    first, _ = await store.remember("il gatto si chiama Nero")
    second, reason = await store.remember("il gatto si chiama Nero")

    assert second == first
    assert reason == "gia' registrato"
    assert len(await store.active()) == 1


async def test_case_does_not_make_it_a_different_fact(store):
    await store.remember("Il Gatto Si Chiama Nero")
    _, reason = await store.remember("il gatto si chiama nero")

    assert reason == "gia' registrato"
    assert len(await store.active()) == 1


async def test_a_forgotten_fact_can_be_remembered_again(store):
    first, _ = await store.remember("abito a Modena")
    await store.forget(first)

    second, reason = await store.remember("abito a Modena")

    assert second != first
    assert reason == ""
    assert len(await store.active()) == 1


# --------------------------------------------------------------------------- #
# The ceiling
# --------------------------------------------------------------------------- #


async def test_the_store_refuses_to_grow_without_limit(store):
    """Without a ceiling the cost of every turn grows until the quota notices."""
    for i in range(MAX_ACTIVE_FACTS):
        assert (await store.remember(f"fatto numero {i}"))[0] is not None

    fact_id, reason = await store.remember("uno di troppo")

    assert fact_id is None
    assert str(MAX_ACTIVE_FACTS) in reason
    assert len(await store.active()) == MAX_ACTIVE_FACTS


async def test_forgetting_one_makes_room_for_another(store):
    for i in range(MAX_ACTIVE_FACTS):
        await store.remember(f"fatto numero {i}")
    await store.forget(1)

    fact_id, reason = await store.remember("adesso ci sta")

    assert fact_id is not None
    assert reason == ""


# --------------------------------------------------------------------------- #
# Forgetting
# --------------------------------------------------------------------------- #


async def test_forgetting_removes_it_from_use(store):
    fact_id, _ = await store.remember("una cosa")

    assert await store.forget(fact_id)
    assert await store.active() == []


async def test_forgetting_does_not_destroy_the_row(store):
    """The same choice as an abandoned job and a corrupt database."""
    fact_id, _ = await store.remember("una cosa")
    await store.forget(fact_id)

    db = store._require_open()
    cursor = await db.execute("SELECT text, forgotten_at FROM facts WHERE id = ?", (fact_id,))
    row = await cursor.fetchone()

    assert row is not None
    assert row[0] == "una cosa"
    assert row[1] is not None


async def test_forgetting_twice_changes_nothing_the_second_time(store):
    fact_id, _ = await store.remember("una cosa")
    await store.forget(fact_id)

    assert not await store.forget(fact_id)


async def test_forgetting_something_that_never_existed_is_not_an_error(store):
    assert not await store.forget(999)


async def test_forgetting_one_leaves_the_others(store):
    a, _ = await store.remember("primo")
    b, _ = await store.remember("secondo")
    c, _ = await store.remember("terzo")

    await store.forget(b)

    assert [f.id for f in await store.active()] == [a, c]


# --------------------------------------------------------------------------- #
# Surviving a restart, which is the whole point
# --------------------------------------------------------------------------- #


async def test_facts_outlive_the_process(tmp_path):
    path = tmp_path / "facts.db"
    first = FactStore(db_path=path)
    await first.open()
    await first.remember("mia figlia si chiama Sara")
    await first.close()

    second = FactStore(db_path=path)
    await second.open()
    try:
        assert [f.text for f in await second.active()] == ["mia figlia si chiama Sara"]
    finally:
        await second.close()


async def test_a_forgotten_fact_stays_forgotten_across_a_restart(tmp_path):
    path = tmp_path / "facts.db"
    first = FactStore(db_path=path)
    await first.open()
    fact_id, _ = await first.remember("una cosa")
    await first.forget(fact_id)
    await first.close()

    second = FactStore(db_path=path)
    await second.open()
    try:
        assert await second.active() == []
    finally:
        await second.close()


async def test_using_it_unopened_says_so(tmp_path):
    """Not an assert: those vanish under `python -O` and leave AttributeError."""
    store = FactStore(db_path=tmp_path / "facts.db")

    with pytest.raises(RuntimeError, match="open"):
        await store.remember("qualcosa")


async def test_the_directory_is_created_if_missing(tmp_path):
    store = FactStore(db_path=tmp_path / "nuova" / "facts.db")
    await store.open()
    try:
        assert (tmp_path / "nuova").is_dir()
    finally:
        await store.close()


# --------------------------------------------------------------------------- #
# Three stores, one file
# --------------------------------------------------------------------------- #
#
# The facts table joins the conversation and the development queue in a single
# SQLite file, because the integrity check, the snapshots and the consistent
# backup were all built around that one file. That is a saving only if the
# arrangement holds: if three connections to it were fragile, the fragility
# would belong to the two that already work, not just to the new one.


@pytest.fixture
async def shared(tmp_path):
    """The three stores, opened on one file, as main.py opens them."""
    path = tmp_path / "data" / "emma.db"
    memory = SqliteConversationMemory(db_path=path, max_messages=20)
    tasks = TaskStore(db_path=path)
    facts = FactStore(db_path=path)
    await memory.open()
    await tasks.open()
    await facts.open()
    yield memory, tasks, facts
    await facts.close()
    await tasks.close()
    await memory.close()


async def test_the_three_stores_share_a_file_without_treading_on_each_other(shared):
    memory, tasks, facts = shared

    await memory.append("c1", StoredMessage(role="user", content="ciao"))
    task_id = await tasks.create("un lavoro")
    fact_id, _ = await facts.remember("un fatto")

    assert len(await memory.get_history("c1")) == 1
    assert [t.id for t in await tasks.open_tasks()] == [task_id]
    assert [f.id for f in await facts.active()] == [fact_id]


async def test_adding_facts_does_not_disturb_the_conversation(shared):
    """The pre-existing behaviour, asserted against the new table."""
    memory, _, facts = shared
    for i in range(30):
        await memory.append("c1", StoredMessage(role="user", content=f"messaggio {i}"))
        await facts.remember(f"fatto {i}")

    history = await memory.get_history("c1")

    assert len(history) <= memory.max_messages
    assert len(await facts.active()) == 30


async def test_the_window_still_prunes_while_facts_do_not(shared):
    """The two halves must keep their opposite policies on forgetting."""
    memory, _, facts = shared
    await facts.remember("questo non deve scadere mai")
    for i in range(60):
        await memory.append("c1", StoredMessage(role="user", content=f"m{i}"))

    assert len(await memory.get_history("c1")) <= memory.max_messages
    assert [f.text for f in await facts.active()] == ["questo non deve scadere mai"]


async def test_the_facts_table_does_not_break_the_integrity_check(shared, tmp_path):
    """The database still has to pass the check the recovery path relies on."""
    _, _, facts = shared
    await facts.remember("un fatto")

    assert await SqliteConversationMemory._file_is_healthy(tmp_path / "data" / "emma.db")


async def test_a_database_with_facts_still_reopens_cleanly(tmp_path):
    """Close everything, reopen everything: the recovery path must not fire."""
    path = tmp_path / "data" / "emma.db"
    for _ in range(2):
        memory = SqliteConversationMemory(db_path=path, max_messages=20)
        tasks = TaskStore(db_path=path)
        facts = FactStore(db_path=path)
        await memory.open()
        await tasks.open()
        await facts.open()
        await facts.remember(f"fatto {_}")
        await facts.close()
        await tasks.close()
        await memory.close()

    assert not list(pathlib.Path(tmp_path / "data").glob("*.corrupt-*"))
