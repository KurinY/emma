"""Tools that let EMMA commission her own development.

Three small capabilities, registered on the router in ``main.py``: record a
request, report what is waiting, and pass an answer back.  Together they are the
user-facing half of the arrangement described in entry 17 of ``REVISIONE.md``;
the other half is a developer reading the same queue from elsewhere.

Each class satisfies the ``Tool`` protocol in :mod:`core.router` structurally --
name, description, schema, ``run`` -- so registering them touches no code in the
router, which is what that protocol was built for.

Two conventions worth knowing before editing:

* **Names are English, descriptions are Italian.**  The name is a code
  identifier and follows the rest of the codebase; the description is prompt
  text a model reads to decide whether to call the tool, and the conversation it
  is deciding within is in Italian.
* **What ``run`` returns is read by the model, not by the user.**  It is a
  compact statement of fact, which EMMA then phrases herself.  Writing polished
  Italian prose here would only get rewritten.
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
    """Render an age in words a model can pass on without doing arithmetic."""
    if seconds < 90:
        return "meno di un minuto fa"
    minutes = seconds / 60
    if minutes < 90:
        return f"{round(minutes)} minuti fa"
    hours = minutes / 60
    if hours < 36:
        return f"{round(hours)} ore fa"
    return f"{round(hours / 24)} giorni fa"


class RequestDevelopment:
    """Record a request to change or extend EMMA's own code."""

    name = "request_development"
    description = (
        "Registra una richiesta di modifica o miglioramento del codice di EMMA, "
        "in modo che uno sviluppatore la prenda in carico. Usalo quando l'utente "
        "chiede una capacita' che al momento non hai, oppure quando scrive un "
        "messaggio che comincia con 'sviluppo:'. Conserva le parole dell'utente "
        "cosi' come sono: non riassumere e non interpretare, chi legge ha il "
        "codice davanti e tu no."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "richiesta": {
                "type": "string",
                "description": (
                    "La richiesta con le parole dell'utente, completa di ogni "
                    "dettaglio utile a capirla senza il resto della conversazione."
                ),
            }
        },
        "required": ["richiesta"],
    }

    def __init__(self, store: TaskStore) -> None:
        """Bind the tool to the queue it writes into."""
        self._store = store

    async def run(self, arguments: dict[str, Any]) -> str:
        """Insert the request and report the number it was given."""
        richiesta = str(arguments.get("richiesta", ""))
        try:
            task_id = await self._store.create(richiesta)
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
        "Elenca i lavori di sviluppo aperti e le domande che attendono una "
        "risposta dell'utente. Usalo quando l'utente chiede a che punto sono i "
        "lavori, se ci sono cose in sospeso, o cosa gli e' stato chiesto."
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
        "Registra la risposta dell'utente a una domanda di un lavoro di "
        "sviluppo, per esempio il consenso a procedere con un passaggio. Serve "
        "il numero del lavoro, che trovi con work_status. Se l'utente risponde "
        "senza dire quale lavoro intende e ce n'e' piu' di uno in attesa, "
        "chiediglielo invece di indovinare."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "numero": {
                "type": "integer",
                "description": "Il numero del lavoro a cui l'utente sta rispondendo.",
            },
            "risposta": {
                "type": "string",
                "description": (
                    "La risposta dell'utente. Riportala fedelmente, comprese le "
                    "condizioni o le correzioni che aggiunge."
                ),
            },
        },
        "required": ["numero", "risposta"],
    }

    def __init__(self, store: TaskStore) -> None:
        """Bind the tool to the queue it writes into."""
        self._store = store

    async def run(self, arguments: dict[str, Any]) -> str:
        """Record the answer, or explain why it could not be recorded."""
        try:
            task_id = int(arguments["numero"])
        except (KeyError, TypeError, ValueError):
            return "Numero del lavoro mancante o non valido: non ho registrato nulla."

        risposta = str(arguments.get("risposta", ""))
        recorded = await self._store.record_answer(task_id, risposta)
        if recorded:
            return f"Risposta registrata sul lavoro #{task_id}."
        return (
            f"Il lavoro #{task_id} non esiste o non ha una domanda in attesa. "
            f"Non ho registrato nulla."
        )


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


def development_tools(store: TaskStore) -> tuple[RequestDevelopment, WorkStatus, AnswerQuestion]:
    """Build the three tools around one store.

    Args:
        store: The queue they all share.

    Returns:
        The tools, ready to hand to the router.
    """
    return (RequestDevelopment(store), WorkStatus(store), AnswerQuestion(store))


__all__ = [
    "STAGE_ORDER",
    "AnswerQuestion",
    "DevelopmentContext",
    "RequestDevelopment",
    "WorkStatus",
    "development_tools",
]
