"""Switching a tool off, and the second request that removes it for good.

Two stages, and the gate between them is here rather than in the personality
prompt on purpose: a rule the model is asked to follow is a rule it can skip,
and this one decides whether code gets deleted. The first request switches a
tool off. The second -- and only when it is already off -- registers the
development job that takes it out of the codebase.

The user proposed the shape (REVISIONE.md, entry 24) and it is better than the
configuration flag I had suggested, for a reason worth writing down: "already
off" is not a formality, it is evidence. It means the tool has been gone for a
while and was not missed, so the irreversible half is never the first thing
that happens.
"""

from __future__ import annotations

import logging
from typing import Any, ClassVar

from core.tasks import TaskStore
from tools.toolstate.store import PROTECTED, ToolStateStore

logger = logging.getLogger(__name__)


class _KnowsTheToolSet:
    """Shared by the tools that must validate a name against the real set.

    Handed the complete tuple after the router is built, because the tools
    exist before it does. A list written by hand here would be a second
    inventory, and the second inventory is always the one that goes stale.
    """

    def __init__(self) -> None:
        self._names: frozenset[str] = frozenset()

    def describes(self, tools: Any) -> None:
        """Take the complete set of registered tools, this one included."""
        self._names = frozenset(t.name for t in tools)


class RemoveTool(_KnowsTheToolSet):
    """Switch a tool off, and on a second request have it removed."""

    name = "remove_tool"
    description = (
        "Remove one of your own tools, in two steps. The first time the user "
        "asks, the tool is only switched off: it stops being offered to you "
        "immediately, and can be switched back on with enable_tool. If they ask "
        "again while it is still off, a development job is registered to take "
        "it out of the code for good. Pass the exact tool name, which list_tools "
        "gives you. Say which of the two steps happened -- the user needs to "
        "know whether anything is still reversible."
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
        super().__init__()
        self._state = state
        self._tasks = tasks

    async def run(self, arguments: dict[str, Any]) -> str:
        """Switch it off, or -- if it already is -- ask for its removal."""
        name = str(arguments.get("name", "")).strip()
        reason = str(arguments.get("reason", "")).strip()

        if not name:
            return "Nome dello strumento mancante: non ho fatto niente."
        if self._names and name not in self._names:
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
                f"Riaccendilo quando vuoi con enable_tool. Se invece dopo averne "
                f"fatto a meno vuoi toglierlo del tutto dal codice, chiedimelo "
                f"di nuovo e registro il lavoro."
            )

        # Already off: this is the second request, so the irreversible half.
        request = f"rimuovere del tutto lo strumento {name} dalla codebase"
        if reason:
            request += f" ({reason})"
        task_id = await self._tasks.create(request)
        logger.info("tool '%s' already off; removal registered as job #%d", name, task_id)
        return (
            f"{name} era gia' disattivato, quindi ho registrato la rimozione "
            f"definitiva come lavoro #{task_id}. Resta spento nel frattempo."
        )


class EnableTool(_KnowsTheToolSet):
    """Switch a tool back on."""

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
        super().__init__()
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
