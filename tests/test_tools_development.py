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
    _describe_age,
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
# All of them together
# --------------------------------------------------------------------------- #


async def test_a_whole_exchange_through_the_tools(store):
    """Commission, be asked something, answer, see it reflected."""
    request_tool, status_tool, answer_tool, _ = development_tools(store)

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


# --------------------------------------------------------------------------- #
# Dropping a job from the chat
# --------------------------------------------------------------------------- #
#
# Commissioned as job #6 on 31 August 2026: until now a request could be made
# from the phone but never taken back, so a job typed by mistake sat in the
# queue asking to be dealt with forever. Nothing is deleted -- the row stays,
# marked abandoned and carrying the reason -- which is the same choice made
# everywhere else here, and for the same reason: a corrupt database is
# quarantined rather than removed, and a decision that can be read back is
# less final than one that cannot.


async def test_an_open_job_can_be_dropped(store):
    _, _, _, abandon = development_tools(store)
    task_id = await store.create("una richiesta sbagliata")

    reply = await abandon.run({"number": task_id, "reason": "l'ho scritta per errore"})

    assert f"#{task_id}" in reply
    assert await store.open_tasks() == []


async def closed_task(store, task_id):
    """Read a task the public queries deliberately no longer return."""
    # _select is private, and reaching for it is the point: everything public
    # filters to open jobs, so nothing else can prove the row survived.
    (task,) = await store._select(f"WHERE id = {int(task_id)}")
    return task


async def test_the_job_is_kept_not_deleted(store):
    """The whole point of abandoning rather than deleting."""
    _, _, _, abandon = development_tools(store)
    task_id = await store.create("una richiesta sbagliata")

    await abandon.run({"number": task_id, "reason": "errore"})

    task = await closed_task(store, task_id)
    assert task.status == "abandoned"
    assert task.request == "una richiesta sbagliata"


async def test_the_reason_is_kept_with_it(store):
    """So a decision taken in one message can be understood a week later."""
    _, _, _, abandon = development_tools(store)
    task_id = await store.create("una richiesta sbagliata")

    await abandon.run({"number": task_id, "reason": "l'ho scritta per errore"})

    assert "l'ho scritta per errore" in (await closed_task(store, task_id)).note


async def test_dropping_it_without_a_reason_still_says_who_asked(store):
    _, _, _, abandon = development_tools(store)
    task_id = await store.create("una richiesta")

    await abandon.run({"number": task_id})

    assert "richiesta dell'utente" in (await closed_task(store, task_id)).note


async def test_the_reply_says_it_was_kept(store):
    """The user is told the decision is reversible, because it is."""
    _, _, _, abandon = development_tools(store)
    task_id = await store.create("una richiesta")

    reply = await abandon.run({"number": task_id})

    assert "non e' stato cancellato" in reply


async def test_a_job_that_does_not_exist_changes_nothing(store):
    _, _, _, abandon = development_tools(store)

    reply = await abandon.run({"number": 999})

    assert "non esiste" in reply
    assert "Non ho abbandonato nulla" in reply


async def test_a_finished_job_is_left_alone(store):
    """Abandoning finished work would rewrite history, not cancel work."""
    _, _, _, abandon = development_tools(store)
    task_id = await store.create("un lavoro concluso")
    await store.finish(task_id, "fatto")

    reply = await abandon.run({"number": task_id})

    assert "non e' piu' aperto" in reply


async def test_dropping_it_twice_is_refused_the_second_time(store):
    _, _, _, abandon = development_tools(store)
    task_id = await store.create("una richiesta")
    await abandon.run({"number": task_id})

    reply = await abandon.run({"number": task_id})

    assert "Non ho abbandonato nulla" in reply


async def test_a_missing_number_drops_nothing(store):
    _, _, _, abandon = development_tools(store)
    task_id = await store.create("una richiesta")

    reply = await abandon.run({})

    assert "non valido" in reply
    assert len(await store.open_tasks()) == 1
    assert (await store.open_tasks())[0].id == task_id


async def test_a_number_that_is_not_a_number_drops_nothing(store):
    _, _, _, abandon = development_tools(store)
    await store.create("una richiesta")

    reply = await abandon.run({"number": "il primo"})

    assert "non valido" in reply
    assert len(await store.open_tasks()) == 1


async def test_a_waiting_job_can_be_dropped_too(store):
    """A job stuck on a question the user no longer wants to answer."""
    _, _, _, abandon = development_tools(store)
    task_id = await store.create("una richiesta")
    await store.advance(task_id, "understood", "quale marca?")

    reply = await abandon.run({"number": task_id, "reason": "lascia perdere"})

    assert f"#{task_id}" in reply
    assert await store.open_tasks() == []


async def test_the_tool_is_declared_to_the_model(store):
    """A tool the model cannot see is a tool that does not exist."""
    _, _, _, abandon = development_tools(store)

    assert abandon.name == "abandon_development"
    assert "number" in abandon.input_schema["required"]
    assert "confirm" in abandon.description.lower()


# --------------------------------------------------------------------------- #
# Saying how long ago something was
# --------------------------------------------------------------------------- #
#
# This text reaches the user through a model that cannot check it, so it has to
# be true on its own. The first threshold was 90 seconds, which meant 89
# seconds was reported as "meno di un minuto fa".


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (0, "meno di un minuto fa"),
        (59, "meno di un minuto fa"),
        (60, "1 minuto fa"),
        (89, "1 minuto fa"),  # was "meno di un minuto fa", which it is not
        (90, "2 minuti fa"),
        (600, "10 minuti fa"),
        (5399, "90 minuti fa"),
        (5400, "2 ore fa"),
        (36000, "10 ore fa"),
        (129599, "36 ore fa"),
        (129600, "2 giorni fa"),
        (864000, "10 giorni fa"),
    ],
)
def test_an_age_is_rendered_the_way_a_person_would_say_it(seconds, expected):
    assert _describe_age(seconds) == expected


def test_the_singular_is_never_written_as_a_plural():
    """A plural on the value one is what nobody notices until a user does."""
    assert _describe_age(60) == "1 minuto fa"


@pytest.mark.parametrize("seconds", [0, 1, 59, 60, 89, 90, 3600, 129600, 10**7])
def test_no_age_is_ever_reported_as_less_than_a_minute_when_it_is_not(seconds):
    """The property behind the fix, stated once rather than per boundary."""
    said_under_a_minute = _describe_age(seconds) == "meno di un minuto fa"

    assert said_under_a_minute == (seconds < 60)
