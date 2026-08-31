"""Tests for the development task queue.

Each test gets its own temporary database file, so they are isolated and leave
nothing behind.  The interesting behaviour is the handover: a task is either the
developer's move or the user's, never both, and it changes hands only through
the two operations meant for it.
"""

from __future__ import annotations

import time

import pytest

from core.tasks import TaskStore


@pytest.fixture
async def store(tmp_path):
    s = TaskStore(db_path=tmp_path / "tasks.db")
    await s.open()
    yield s
    await s.close()


async def test_a_new_store_is_empty(store):
    assert await store.open_tasks() == []
    assert await store.queued() == []
    assert await store.awaiting_user() == []


async def test_a_new_task_waits_for_the_developer(store):
    task_id = await store.create("aggiungi i promemoria")

    queued = await store.queued()
    assert [t.id for t in queued] == [task_id]
    assert queued[0].stage == "new"
    assert queued[0].status == "queued"
    # Nothing is asked of the user until somebody has looked at it.
    assert await store.awaiting_user() == []


async def test_the_request_is_stored_verbatim(store):
    words = "vorrei che ricordassi i miei appuntamenti, anche quelli ricorrenti"
    task_id = await store.create(f"  {words}  ")

    (task,) = await store.queued()
    assert task.id == task_id
    assert task.request == words


async def test_an_empty_request_is_refused(store):
    with pytest.raises(ValueError, match="cannot be empty"):
        await store.create("   ")
    assert await store.open_tasks() == []


async def test_advancing_hands_the_task_to_the_user(store):
    task_id = await store.create("aggiungi i promemoria")

    assert await store.advance(task_id, "understood", "ho capito cosi'. Procedo?")

    assert await store.queued() == []
    (waiting,) = await store.awaiting_user()
    assert waiting.stage == "understood"
    assert waiting.status == "waiting_user"
    assert waiting.note == "ho capito cosi'. Procedo?"


async def test_answering_hands_it_back_to_the_developer(store):
    task_id = await store.create("aggiungi i promemoria")
    await store.advance(task_id, "understood", "Procedo?")

    assert await store.record_answer(task_id, "si', procedi")

    assert await store.awaiting_user() == []
    (queued,) = await store.queued()
    assert queued.answer == "si', procedi"
    # The stage does not move backwards when an answer arrives.
    assert queued.stage == "understood"


async def test_a_new_question_clears_the_previous_answer(store):
    """Otherwise a stale 'yes' would sit next to the next question."""
    task_id = await store.create("aggiungi i promemoria")
    await store.advance(task_id, "understood", "Procedo?")
    await store.record_answer(task_id, "si'")

    await store.advance(task_id, "implemented", "Committo?")

    (waiting,) = await store.awaiting_user()
    assert waiting.answer == ""
    assert waiting.note == "Committo?"


async def test_answering_a_task_that_asked_nothing_is_refused(store):
    task_id = await store.create("aggiungi i promemoria")

    assert await store.record_answer(task_id, "si'") is False
    assert await store.record_answer(9999, "si'") is False


async def test_an_empty_answer_is_refused(store):
    task_id = await store.create("aggiungi i promemoria")
    await store.advance(task_id, "understood", "Procedo?")

    assert await store.record_answer(task_id, "  ") is False
    # Still the user's move.
    assert len(await store.awaiting_user()) == 1


async def test_an_unknown_stage_is_refused(store):
    task_id = await store.create("aggiungi i promemoria")

    with pytest.raises(ValueError, match="unknown stage"):
        await store.advance(task_id, "quasi_fatto", "Procedo?")


async def test_advancing_a_task_that_does_not_exist_reports_it(store):
    assert await store.advance(9999, "understood", "Procedo?") is False


async def test_the_full_journey(store):
    """Every checkpoint in order, ending deployed and out of the open list."""
    task_id = await store.create("aggiungi i promemoria")

    for stage, question in (
        ("understood", "Procedo?"),
        ("implemented", "Committo?"),
        ("committed", "Pusho?"),
        ("pushed", "Deployo?"),
    ):
        assert await store.advance(task_id, stage, question)
        (waiting,) = await store.awaiting_user()
        assert waiting.stage == stage
        assert await store.record_answer(task_id, "si'")

    assert await store.finish(task_id, "fatto")

    assert await store.open_tasks() == []
    assert await store.awaiting_user() == []


async def test_an_abandoned_task_stops_asking(store):
    task_id = await store.create("una cosa che non serve piu'")
    await store.advance(task_id, "understood", "Procedo?")

    assert await store.abandon(task_id, "non serve piu'")

    assert await store.open_tasks() == []
    assert await store.awaiting_user() == []


async def test_conversations_are_listed_oldest_first(store):
    first = await store.create("primo")
    second = await store.create("secondo")

    assert [t.id for t in await store.open_tasks()] == [first, second]


async def test_the_heartbeat_starts_unset(store):
    assert await store.last_seen() is None


async def test_touching_records_the_time(store):
    before = time.time()
    await store.touch()
    seen = await store.last_seen()

    assert seen is not None
    assert seen >= before


async def test_touching_twice_updates_rather_than_duplicating(store):
    await store.touch()
    first = await store.last_seen()
    await store.touch()
    second = await store.last_seen()

    assert second is not None and first is not None
    assert second >= first


async def test_tasks_survive_a_reopen(tmp_path):
    db = tmp_path / "persist.db"

    s1 = TaskStore(db_path=db)
    await s1.open()
    task_id = await s1.create("deve sopravvivere")
    await s1.advance(task_id, "understood", "Procedo?")
    await s1.close()

    s2 = TaskStore(db_path=db)
    await s2.open()
    (waiting,) = await s2.awaiting_user()
    await s2.close()

    assert waiting.request == "deve sopravvivere"
    assert waiting.note == "Procedo?"


async def test_using_the_store_before_opening_it_says_so(tmp_path):
    s = TaskStore(db_path=tmp_path / "closed.db")

    with pytest.raises(RuntimeError, match="call open"):
        await s.create("qualcosa")


async def test_the_store_shares_a_file_with_the_memory(tmp_path):
    """The two open the same database and must not disturb each other."""
    from core.memory import SqliteConversationMemory, StoredMessage

    db = tmp_path / "shared.db"

    memory = SqliteConversationMemory(db_path=db, max_messages=10)
    tasks = TaskStore(db_path=db)
    await memory.open()
    await tasks.open()

    await memory.append("c1", StoredMessage(role="user", content="ciao"))
    task_id = await tasks.create("un lavoro")

    history = await memory.get_history("c1")
    open_tasks = await tasks.open_tasks()

    await tasks.close()
    await memory.close()

    assert [m.content for m in history] == ["ciao"]
    assert [t.id for t in open_tasks] == [task_id]
