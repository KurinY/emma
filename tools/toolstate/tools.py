"""Switching a tool off, and the second request that removes it for good.

Two stages, and the gate between them is here rather than in the personality
prompt on purpose: a rule the model is asked to follow is a rule it can skip,
and this one decides whether code gets deleted. The first request switches a
tool off. The second -- and only when it has been off long enough to count --
registers the development job that takes it out of the codebase.

The user proposed the shape (REVISIONE.md, entry 24) and it is better than the
configuration flag I had suggested, for a reason worth writing down: "already
off" is not a formality, it is evidence. It means the tool has been gone a
while and was not missed, so the irreversible half is never the first thing
that happens.

That last sentence was a claim before it was true. The first version branched
on "is it already off" and nothing else, and a turn allows several tool rounds
-- so the model could switch a tool off and ask for its removal in the same
breath. What the code enforced was two calls, not two occasions. The threshold
in :data:`tools.toolstate.store.MIN_TIME_OFF_SECONDS` is what closed that gap.
"""

from __future__ import annotations

import logging
import time
from typing import Any, ClassVar

from core.tasks import TaskStore
from tools.toolstate.store import MIN_TIME_OFF_SECONDS, PROTECTED, ToolStateStore

logger = logging.getLogger(__name__)


def _in_words(seconds: float) -> str:
    """Say how long is left, the way somebody would say it."""
    if seconds < 90:
        return "meno di un minuto"
    minutes = seconds / 60
    if minutes < 90:
        whole = round(minutes)
        return "1 minuto" if whole == 1 else f"{whole} minuti"
    hours = round(minutes / 60)
    return "1 ora" if hours == 1 else f"{hours} ore"


class RemoveTool:
    """Switch a tool off, and on a later request have it removed."""

    name = "remove_tool"
    description = (
        "Remove one of your own tools, in two steps. The first time the user "
        "asks, the tool is only switched off: it stops being offered to you and "
        "can be switched back on with enable_tool. Asking again later -- not in "
        "the same conversation -- registers a development job to take it out of "
        "the code for good. Pass the exact tool name, which list_tools gives "
        "you. Say which of the two steps happened: the user needs to know "
        "whether anything is still reversible."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Exact name of the tool."},
            "reason": {
                "type": "string",
                "description": "Why, in the user's own words, when they gave one.",
            },
        },
        "required": ["name"],
    }

    def __init__(self, state: ToolStateStore, tasks: TaskStore) -> None:
        """Bind to the switch and to the queue the second stage writes into."""
        self._state = state
        self._tasks = tasks
        self._names: frozenset[str] = frozenset()

    def describes(self, tools: Any) -> None:
        """Take the complete set of registered tools, this one included.

        Handed over after the router is built, because the tools exist before
        it does. A list written by hand here would be a second inventory, and
        the second inventory is always the one that goes stale.
        """
        self._names = frozenset(t.name for t in tools)

    async def _existing_job(self, name: str) -> int | None:
        """The open removal job for this tool, if one was already filed.

        Without this, every further request opens another identical job and the
        queue fills with the same sentence.
        """
        marker = _removal_request(name)
        for job in await self._tasks.open_tasks():
            if job.request.startswith(marker):
                return job.id
        return None

    async def run(self, arguments: dict[str, Any]) -> str:
        """Switch it off, or -- if it has been off long enough -- ask for its removal."""
        name = str(arguments.get("name", "")).strip()
        reason = str(arguments.get("reason", "")).strip()

        if not name:
            return "Nome dello strumento mancante: non ho fatto niente."

        # Refused rather than skipped when the set was never handed over. The
        # first version skipped validation in that case, so a wiring mistake
        # turned into "switch off a tool that does not exist" instead of an
        # error anybody would see.
        if not self._names:  # pragma: no cover - a wiring mistake, not a state
            return "Non lo so fare adesso: l'elenco degli strumenti non mi e' stato passato."
        if name not in self._names:
            return (
                f"Non ho nessuno strumento che si chiami {name}. Controlla il nome con list_tools."
            )
        if name in PROTECTED:
            return (
                f"Non posso disattivare {name}: senza di lui non sapresti piu' "
                f"cosa e' spento ne' come riaccenderlo."
            )

        if await self._state.disable(name, reason):
            logger.info("tool '%s' switched off", name)
            return (
                f"Ho disattivato {name}: da adesso non lo uso piu'. "
                f"Riaccendilo quando vuoi con enable_tool. Se piu' avanti, dopo "
                f"averne fatto a meno, vuoi toglierlo del tutto dal codice, "
                f"chiedimelo di nuovo."
            )

        # Already off. Two things still have to hold before the irreversible
        # half: it must have been off long enough to have been missed, and
        # nobody must have asked for this already.
        since = await self._state.disabled_since(name)
        if since is not None:
            waited = time.time() - since
            if waited < MIN_TIME_OFF_SECONDS:
                left = MIN_TIME_OFF_SECONDS - waited
                logger.info("removal of '%s' asked too soon (%.0fs off)", name, waited)
                return (
                    f"{name} e' disattivato solo da {_in_words(waited)}. Per "
                    f"registrare la rimozione definitiva aspetto che sia passato "
                    f"un po' di tempo senza di lui: riprova fra {_in_words(left)}. "
                    f"Nel frattempo resta spento."
                )

        if (existing := await self._existing_job(name)) is not None:
            return (
                f"La rimozione di {name} e' gia' il lavoro #{existing}. Resta spento nel frattempo."
            )

        request = _removal_request(name)
        if reason:
            request += f" ({reason})"
        # The row survives the tool it names, and nobody would think of it a
        # week later, so the job says to clear it.
        request += ". Togliere anche la sua riga dalla tabella tool_state."
        task_id = await self._tasks.create(request)
        logger.info("removal of '%s' registered as job #%d", name, task_id)
        return (
            f"{name} era gia' disattivato da un po', quindi ho registrato la "
            f"rimozione definitiva come lavoro #{task_id}. Resta spento nel frattempo."
        )


def _removal_request(name: str) -> str:
    """The opening of a removal job, used to write one and to find it again."""
    return f"rimuovere del tutto lo strumento {name} dalla codebase"


class EnableTool:
    """Switch a tool back on.

    Deliberately does not validate the name against the registered set, and so
    deliberately does not take that set. A row can outlive the tool it names --
    after a removal job is carried out, or after a rename -- and this is the
    only way to clear one without touching the server.
    """

    name = "enable_tool"
    description = (
        "Switch a tool back on after it was disabled with remove_tool. Pass the "
        "exact name; list_tools shows which ones are currently off. Use it "
        "whenever the user wants a capability back."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {"name": {"type": "string", "description": "Exact name of the tool."}},
        "required": ["name"],
    }

    def __init__(self, state: ToolStateStore) -> None:
        """Bind to the switch."""
        self._state = state

    async def run(self, arguments: dict[str, Any]) -> str:
        """Switch it on, or say why nothing changed."""
        name = str(arguments.get("name", "")).strip()
        if not name:
            return "Nome dello strumento mancante: non ho fatto niente."

        if await self._state.enable(name):
            logger.info("tool '%s' switched back on", name)
            return f"Ho riattivato {name}: torno a poterlo usare da subito."
        return f"{name} non era disattivato, quindi non ho cambiato niente."


def toolstate_tools(state: ToolStateStore, tasks: TaskStore) -> tuple[RemoveTool, EnableTool]:
    """Build the two tools around the switch and the queue.

    Args:
        state: Where the switched-off names live.
        tasks: The development queue the second stage writes into.

    Returns:
        The tools, ready to hand to the router.
    """
    return (RemoveTool(state, tasks), EnableTool(state))


__all__ = ["EnableTool", "RemoveTool", "toolstate_tools"]
