"""Tests for the three development tools.

They run against a real store on a temporary file rather than a mock: the
tools are thin, so what is worth checking is the behaviour the pair produces
together, and a mock would only assert that the code calls what it calls.

The strings returned by ``run`` are read by a model, not by the user, so the
assertions look for the facts in them and not for exact wording.
"""

from __future__ import annotations

import time

import pytest

from core.router import Tool
from core.tasks import TaskStore
from tools.development import (
    AnswerQuestion,
    RequestDevelopment,
    WorkStatus,
    development_tools,
)


@pytest.fixture
async def store(tmp_path):
    s = TaskStore(db_path=tmp_path / "tools.db")
    await s.open()
    yield s
    await s.close()


async def test_they_satisfy_the_tool_protocol(store):
    for tool in development_tools(store):
        assert isinstance(tool, Tool)


async def test_their_names_are_distinct(store):
    names = [tool.name for tool in development_tools(store)]
    assert len(set(names)) == len(names)


async def test_every_schema_is_a_json_object(store):
    for tool in development_tools(store):
        assert tool.input_schema["type"] == "object"
        assert "properties" in tool.input_schema


# --------------------------------------------------------------------------- #
# request_development
# --------------------------------------------------------------------------- #


async def test_requesting_development_records_the_task(store):
    tool = RequestDevelopment(store)

    result = await tool.run({"request": "vorrei i promemoria"})

    (task,) = await store.open_tasks()
    assert task.request == "vorrei i promemoria"
    assert str(task.id) in result


async def test_an_empty_request_records_nothing(store):
    tool = RequestDevelopment(store)

    result = await tool.run({"request": "   "})

    assert await store.open_tasks() == []
    assert "vuota" in result.lower()


async def test_a_missing_argument_records_nothing(store):
    tool = RequestDevelopment(store)

    await tool.run({})

    assert await store.open_tasks() == []


# --------------------------------------------------------------------------- #
# work_status
# --------------------------------------------------------------------------- #


async def test_status_of_an_empty_queue(store):
    result = await WorkStatus(store).run({})
    assert "nessun lavoro" in result.lower()


async def test_status_lists_the_open_tasks(store):
    await store.create("primo lavoro")
    await store.create("secondo lavoro")
    await store.touch()

    result = await WorkStatus(store).run({})

    assert "primo lavoro" in result
    assert "secondo lavoro" in result


async def test_status_surfaces_a_pending_question(store):
    task_id = await store.create("un lavoro")
    await store.advance(task_id, "implemented", "test verdi. Committo?")
    await store.touch()

    result = await WorkStatus(store).run({})

    assert "Committo?" in result
    assert "ATTENDE UNA RISPOSTA" in result


async def test_the_question_comes_before_the_original_request(store):
    """A model told to be brief keeps the beginning, so the question leads.

    Leading with the request once cost the user a wrong answer: EMMA relayed
    their own ambiguous wording and dropped the clarification that resolved it.
    """
    task_id = await store.create("una richiesta scritta in modo ambiguo")
    await store.advance(task_id, "understood", "Ho capito cosi: X. Procedo?")
    await store.touch()

    result = await WorkStatus(store).run({})

    assert result.index("Ho capito cosi") < result.index("ambiguo")


async def test_every_task_is_listed_when_several_wait(store):
    """Reporting only one of them is the failure this guards against."""
    first = await store.create("il primo lavoro")
    second = await store.create("il secondo lavoro")
    await store.advance(first, "understood", "Prima domanda. Procedo?")
    await store.advance(second, "understood", "Seconda domanda. Procedo?")
    await store.touch()

    result = await WorkStatus(store).run({})

    assert f"#{first}" in result and f"#{second}" in result
    assert "Prima domanda" in result and "Seconda domanda" in result


async def test_the_listing_tells_the_model_not_to_summarise(store):
    """The personality says be brief; this answer is the exception."""
    task_id = await store.create("un lavoro")
    await store.advance(task_id, "understood", "Procedo?")
    await store.touch()

    result = await WorkStatus(store).run({})

    assert "non riassumere" in result.lower()


async def test_a_long_request_is_shortened_but_the_question_is_not(store):
    long_request = "parola " * 60
    task_id = await store.create(long_request)
    long_note = "Nota lunga che deve arrivare intera. " * 6
    await store.advance(task_id, "understood", long_note)
    await store.touch()

    result = await WorkStatus(store).run({})

    assert "..." in result
    assert long_note.strip() in result


async def test_status_hides_finished_work(store):
    done = await store.create("finito")
    await store.finish(done)
    await store.create("ancora aperto")
    await store.touch()

    result = await WorkStatus(store).run({})

    assert "ancora aperto" in result
    assert "finito" not in result


async def test_status_warns_when_no_session_has_ever_looked(store):
    await store.create("un lavoro")

    result = await WorkStatus(store).run({})

    assert "NOTA" in result
    assert "non e' attiva" in result


async def test_status_warns_when_the_session_went_quiet(store, monkeypatch):
    """A session that dies takes the whole mechanism with it, silently."""
    await store.create("un lavoro")
    await store.touch()

    # Two days later, from the point of view of the tool.
    real_time = time.time
    monkeypatch.setattr(time, "time", lambda: real_time() + 2 * 24 * 3600)

    result = await WorkStatus(store).run({})

    assert "NOTA" in result
    assert "giorni fa" in result


async def test_status_stays_quiet_when_the_session_is_alive(store):
    await store.create("un lavoro")
    await store.touch()

    result = await WorkStatus(store).run({})

    assert "NOTA" not in result


# --------------------------------------------------------------------------- #
# answer_question
# --------------------------------------------------------------------------- #


async def test_answering_records_the_reply(store):
    task_id = await store.create("un lavoro")
    await store.advance(task_id, "committed", "Pusho?")

    result = await AnswerQuestion(store).run({"number": task_id, "answer": "si'"})

    (task,) = await store.queued()
    assert task.answer == "si'"
    assert str(task_id) in result


async def test_answering_accepts_a_numeric_string(store):
    """Models routinely hand back "3" where the schema says integer."""
    task_id = await store.create("un lavoro")
    await store.advance(task_id, "committed", "Pusho?")

    await AnswerQuestion(store).run({"number": str(task_id), "answer": "si'"})

    (task,) = await store.queued()
    assert task.answer == "si'"


async def test_answering_an_unknown_task_says_so(store):
    result = await AnswerQuestion(store).run({"number": 9999, "answer": "si'"})
    assert "non esiste" in result


async def test_answering_without_a_number_says_so(store):
    result = await AnswerQuestion(store).run({"answer": "si'"})
    assert "mancante" in result.lower()


async def test_answering_a_task_with_no_question_pending(store):
    task_id = await store.create("un lavoro")

    result = await AnswerQuestion(store).run({"number": task_id, "answer": "si'"})

    assert "non ha una domanda" in result


# --------------------------------------------------------------------------- #
# The three of them together
# --------------------------------------------------------------------------- #


async def test_a_whole_exchange_through_the_tools(store):
    """Commission, be asked something, answer, see it reflected."""
    request_tool, status_tool, answer_tool = development_tools(store)

    await request_tool.run({"request": "vorrei i promemoria"})
    (task,) = await store.open_tasks()

    # The developer reaches the first checkpoint.
    await store.advance(task.id, "understood", "ho capito cosi'. Procedo?")
    await store.touch()

    status = await status_tool.run({})
    assert "Procedo?" in status

    await answer_tool.run({"number": task.id, "answer": "si', procedi"})

    (after,) = await store.queued()
    assert after.answer == "si', procedi"
    assert "Procedo?" not in await status_tool.run({})
