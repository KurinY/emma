"""Tools that let EMMA commission her own development.

Three small capabilities, registered on the router in ``main.py``: record a
request, report what is waiting, and pass an answer back.  Together they are the
user-facing half of the arrangement described in entry 17 of ``REVISIONE.md``;
the other half is a developer reading the same queue from elsewhere.

Each class satisfies the ``Tool`` protocol in :mod:`core.router` structurally --
name, description, schema, ``run`` -- so registering them touches no code in the
router, which is what that protocol was built for.

One convention worth knowing before editing, because the line is not where it
first looks:

* **What the model reads in order to decide is English** -- names, descriptions,
  schema properties. None of it ever reaches the user, so it follows the rest
  of the codebase.
* **What ``run`` returns is Italian**, because the user does see it. In theory
  EMMA rephrases these strings; in practice she quotes them verbatim, and a
  listing written in English surfaced English in an Italian chat. The same
  applies to :data:`_STAGE_LABELS` and to anything a provider puts in front of
  the model, since that gets repeated too.
"""

from __future__ import annotations

import time
from typing import Any, ClassVar

from core.tasks import STAGE_ORDER, Task, TaskStore

#: After this long without the developer's session looking at the queue,
#: something is wrong: it is normally polled every few minutes.  Six hours is
#: loose enough to survive a laptop lid and short enough to notice a session
#: that died overnight.
STALE_AFTER_SECONDS = 6 * 60 * 60

#: What each stage means to the user, and what the pending question is about.
_STAGE_LABELS: dict[str, str] = {
    "new": "in attesa che lo sviluppatore lo prenda in carico",
    "understood": "capito, in attesa di procedere con l'implementazione",
    "implemented": "implementato e testato, in attesa di essere committato",
    "committed": "committato in locale, in attesa di essere pubblicato",
    "pushed": "pubblicato su GitHub, in attesa del deploy",
    "deployed": "deployato",
}


def _shorten(text: str, limit: int = 90) -> str:
    """Trim long text for the context half of a listing line."""
    text = text.strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "..."


def _describe_age(seconds: float) -> str:
    """Render an age in words a model can pass on without doing arithmetic.

    The thresholds are picked so the sentence is true rather than tidy. The
    first one used to be 90 seconds, so anything up to a minute and a half was
    reported as "meno di un minuto fa" -- which of 89 seconds is simply false,
    and this text goes to the user through a model that cannot check it.
    """
    if seconds < 60:
        return "meno di un minuto fa"

    minutes = seconds / 60
    if minutes < 90:
        whole = round(minutes)
        return "1 minuto fa" if whole == 1 else f"{whole} minuti fa"

    hours = minutes / 60
    if hours < 36:
        whole = round(hours)
        return "1 ora fa" if whole == 1 else f"{whole} ore fa"

    days = round(hours / 24)
    return "1 giorno fa" if days == 1 else f"{days} giorni fa"


class RequestDevelopment:
    """Record a request to change or extend EMMA's own code."""

    name = "request_development"
    description = (
        "Record a request to change or extend EMMA's own code, so that a "
        "developer picks it up. Use it when the user asks for a capability you "
        "do not have, or when their message begins with 'sviluppo:'. Keep the "
        "user's own words: do not summarise and do not interpret, because "
        "whoever reads the request has the code in front of them and you do not."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "request": {
                "type": "string",
                "description": (
                    "The request in the user's own words, complete enough to be "
                    "understood without the rest of the conversation."
                ),
            }
        },
        "required": ["request"],
    }

    def __init__(self, store: TaskStore) -> None:
        """Bind the tool to the queue it writes into."""
        self._store = store

    async def run(self, arguments: dict[str, Any]) -> str:
        """Insert the request and report the number it was given."""
        request = str(arguments.get("request", ""))
        try:
            task_id = await self._store.create(request)
        except ValueError:
            return "Richiesta vuota: non e' stato registrato nulla."
        return (
            f"Registrata come lavoro #{task_id}. "
            f"Sara' presa in carico alla prossima sessione di sviluppo."
        )


class WorkStatus:
    """Report open development work and any question awaiting the user."""

    name = "work_status"
    description = (
        "List the open development jobs and any question waiting on the user. "
        "Use it whenever the user asks how the work is going, whether anything "
        "is pending, or what they have been asked. Do not answer from memory: "
        "the state changes between one question and the next."
    )
    input_schema: ClassVar[dict[str, Any]] = {"type": "object", "properties": {}}

    def __init__(self, store: TaskStore) -> None:
        """Bind the tool to the queue it reads."""
        self._store = store

    async def run(self, arguments: dict[str, Any]) -> str:
        """Summarise the open tasks, the pending questions and the heartbeat."""
        tasks = await self._store.open_tasks()
        if not tasks:
            return "Nessun lavoro di sviluppo aperto."

        # The personality tells EMMA to answer in two or three short sentences,
        # which is right for conversation and wrong for this: a model handed a
        # long list under a brevity instruction compresses it, and what gets
        # dropped is a whole task or the question inside one. Say plainly, here
        # where the model is reading, that this particular answer is a list.
        waiting = sum(1 for t in tasks if t.status == "waiting_user")
        header = (
            f"{len(tasks)} lavori di sviluppo aperti."
            if len(tasks) > 1
            else "1 lavoro di sviluppo aperto."
        )
        header += (
            " Riferiscili TUTTI all'utente, ognuno con il suo numero, e riporta"
            " ogni domanda per intero: qui non riassumere e non sceglierne uno."
        )
        if waiting:
            header += f" {waiting} attendono una sua risposta."

        lines = [header, ""]
        lines.extend(self._describe(task) for task in tasks)

        warning = await self._staleness_warning()
        if warning:
            lines.append(warning)

        return "\n".join(lines)

    def _describe(self, task: Task) -> str:
        """Render one task, question first when there is one.

        The order matters more than it looks. A model asked to be brief keeps
        the beginning of what it is given, so whatever leads is what survives.
        For a task waiting on an answer the question is the useful half; the
        original wording is context, and often the ambiguous phrasing the
        question exists to resolve. Leading with the request once cost the user
        a wrong answer about their own task.
        """
        if task.status == "waiting_user" and task.note:
            return (
                f"#{task.id} ATTENDE UNA RISPOSTA DELL'UTENTE.\n"
                f"   Domanda da riferire per intero: {task.note}\n"
                f"   (la richiesta originale era: {_shorten(task.request)})"
            )
        label = _STAGE_LABELS.get(task.stage, task.stage)
        return f"#{task.id} [{label}] {task.request}"

    async def _staleness_warning(self) -> str:
        """Say so when nobody has looked at the queue for a long time.

        There is no service behind this queue, only a session someone left
        open.  When it dies the requests keep piling up and nothing complains,
        so the absence of a heartbeat is itself the news.
        """
        seen = await self._store.last_seen()
        if seen is None:
            return (
                "NOTA: nessuna sessione di sviluppo ha ancora letto la coda. "
                "Se ci sono lavori in attesa, la sessione non e' attiva."
            )
        age = time.time() - seen
        if age > STALE_AFTER_SECONDS:
            return (
                f"NOTA: l'ultimo contatto con la sessione di sviluppo risale a "
                f"{_describe_age(age)}. Probabilmente non e' attiva."
            )
        return ""


class AnswerQuestion:
    """Pass the user's answer back to the developer."""

    name = "answer_question"
    description = (
        "Record the user's answer to a question on a development job -- for "
        "instance their consent to proceed with a step. It needs the job "
        "number, which work_status gives you. If the user answers without "
        "saying which job they mean and more than one is waiting, ask them "
        "rather than guess."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "number": {
                "type": "integer",
                "description": "The number of the job the user is answering.",
            },
            "answer": {
                "type": "string",
                "description": (
                    "The user's answer, reported faithfully, including any "
                    "condition or correction they attach to it."
                ),
            },
        },
        "required": ["number", "answer"],
    }

    def __init__(self, store: TaskStore) -> None:
        """Bind the tool to the queue it writes into."""
        self._store = store

    async def run(self, arguments: dict[str, Any]) -> str:
        """Record the answer, or explain why it could not be recorded."""
        try:
            task_id = int(arguments["number"])
        except (KeyError, TypeError, ValueError):
            return "Numero del lavoro mancante o non valido: non ho registrato nulla."

        answer = str(arguments.get("answer", ""))
        recorded = await self._store.record_answer(task_id, answer)
        if recorded:
            return f"Risposta registrata sul lavoro #{task_id}."
        return (
            f"Il lavoro #{task_id} non esiste o non ha una domanda in attesa. "
            f"Non ho registrato nulla."
        )


class AbandonDevelopment:
    """Drop a job the user no longer wants, from the chat."""

    name = "abandon_development"
    description = (
        "Drop a development job the user no longer wants, so it stops asking "
        "to be dealt with. It needs the job number, which work_status gives "
        "you. Only jobs that are still open can be dropped: one already "
        "finished stays as it is. Ask the user to confirm before calling this, "
        "and say which job you are about to drop, unless they already named "
        "the number themselves."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "number": {
                "type": "integer",
                "description": "The number of the job to drop.",
            },
            "reason": {
                "type": "string",
                "description": (
                    "Why it is being dropped, in the user's own words when "
                    "they gave one. Kept with the job, which is not deleted."
                ),
            },
        },
        "required": ["number"],
    }

    def __init__(self, store: TaskStore) -> None:
        """Bind the tool to the queue it writes into."""
        self._store = store

    async def run(self, arguments: dict[str, Any]) -> str:
        """Drop the job, or explain why nothing was dropped.

        Nothing is deleted: the row stays, marked abandoned and carrying the
        reason. The same choice as everywhere else here -- a corrupt database
        is quarantined rather than removed -- and for the same reason. A user
        who changes their mind an hour later has something to change it back
        from, and a request dropped by mistake is still legible.
        """
        try:
            task_id = int(arguments["number"])
        except (KeyError, TypeError, ValueError):
            return "Numero del lavoro mancante o non valido: non ho abbandonato nulla."

        # Only open jobs.  Abandoning something already finished would rewrite
        # history rather than cancel work, and the queue is the only record of
        # what was asked.
        open_now = {task.id for task in await self._store.open_tasks()}
        if task_id not in open_now:
            return (
                f"Il lavoro #{task_id} non esiste o non e' piu' aperto. Non ho abbandonato nulla."
            )

        reason = str(arguments.get("reason", "")).strip()
        note = (
            f"Abbandonato su richiesta dell'utente: {reason}"
            if reason
            else ("Abbandonato su richiesta dell'utente.")
        )
        if await self._store.abandon(task_id, note):
            return f"Lavoro #{task_id} abbandonato. Resta registrato, non e' stato cancellato."
        return f"Non sono riuscita ad abbandonare il lavoro #{task_id}."


class DevelopmentContext:
    """Puts the current shape of the queue in front of the model every turn.

    Satisfies the ``ContextProvider`` protocol in :mod:`core.router`, and
    exists for the reason set out there: whether ``work_status`` gets called is
    the model's decision, and it decided wrong four times in ten when a stale
    answer was sitting in the conversation -- repeating it word for word rather
    than looking. A line that is simply present cannot be skipped.

    It reports counts and numbers only. The details stay behind the tool: this
    is paid for on every message, including the overwhelming majority that have
    nothing to do with development work.
    """

    def __init__(self, store: TaskStore) -> None:
        """Bind the provider to the queue it reads."""
        self._store = store

    async def snapshot(self) -> str:
        """Return one line describing the queue, or nothing when it is empty."""
        tasks = await self._store.open_tasks()
        if not tasks:
            return (
                "Stato dei lavori di sviluppo in questo momento: nessuno aperto. "
                "Questa riga e' sempre aggiornata: se ricordi il contrario, "
                "questa ha ragione."
            )

        waiting = [t for t in tasks if t.status == "waiting_user"]
        line = (
            f"Stato dei lavori di sviluppo in questo momento: {len(tasks)} aperti "
            f"({', '.join('#' + str(t.id) for t in tasks)})."
        )
        if waiting:
            line += (
                f" Di questi, {len(waiting)} attendono una risposta dell'utente"
                f" ({', '.join('#' + str(t.id) for t in waiting)})."
            )
        line += (
            " Questa riga e' sempre aggiornata: se la conversazione precedente"
            " dice un numero diverso, quella e' vecchia e questa ha ragione."
            " Per il contenuto dei lavori usa work_status, non la memoria."
        )
        return line


def development_tools(
    store: TaskStore,
) -> tuple[RequestDevelopment, WorkStatus, AnswerQuestion, AbandonDevelopment]:
    """Build the development tools around one store.

    Args:
        store: The queue they all share.

    Returns:
        The tools, ready to hand to the router.
    """
    return (
        RequestDevelopment(store),
        WorkStatus(store),
        AnswerQuestion(store),
        AbandonDevelopment(store),
    )


__all__ = [
    "STAGE_ORDER",
    "AbandonDevelopment",
    "AnswerQuestion",
    "DevelopmentContext",
    "RequestDevelopment",
    "WorkStatus",
    "development_tools",
]
