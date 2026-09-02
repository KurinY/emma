"""Which tools are switched off, and the two-stage road to removing one.

Removing a tool means editing the composition root and deploying: a development
job, and not one that can be undone in a hurry. Switching it off is reversible,
takes effect on the next tool round and survives a restart, which makes it the
right first move -- and, once it has been off for a while without being missed,
the evidence that removing it was really wanted.

The user proposed the two stages and the shape is theirs (REVISIONE.md, entry
24): the first request switches a tool off; the second, **only if it is already
off**, registers the job that takes it out of the code. The gate is here rather
than in the personality prompt because a rule the model is asked to follow is a
rule it can skip, and this one decides whether code gets deleted.

The table shares the SQLite file with the conversation, the development queue
and the facts, for the reason in entry 17: the integrity check, the snapshots
and the consistent backup were built around one file.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    import aiosqlite as aiosqlite_t

#: Tools that may never be switched off.
#:
#: Not a matter of taste: without ``list_tools`` the user cannot see what is
#: off, and without ``enable_tool`` they cannot put it back. Allowing either to
#: be disabled would let the assistant lock the door and lose the key, and the
#: only way out would be an edit on the server.
#:
#: ``remove_tool`` is deliberately *not* here. Switching it off is the one
#: disable the mechanism cannot undo by itself -- but ``enable_tool`` still
#: can, so the user is never stuck, and protecting a tool from itself would be
#: a rule with no failure to prevent.
PROTECTED = frozenset({"list_tools", "enable_tool"})

#: How long a tool must have been off before its removal can be commissioned.
#:
#: Without this the second stage is a *counter*, not evidence: a turn allows
#: several tool rounds, so the model can switch a tool off and ask for its
#: removal in the same breath, having learnt nothing in between. The module
#: docstring, the changelog and the guide all promise that "already off" means
#: it was gone a while and was not missed; this is what makes that true.
#:
#: An hour rather than a day: long enough that no single conversation can cross
#: it, short enough that somebody who has genuinely decided is not blocked
#: until tomorrow. The refusal says how much is left, so the wait is never a
#: mystery.
MIN_TIME_OFF_SECONDS = 3600.0


@dataclass(frozen=True, slots=True)
class SwitchedOff:
    """A tool that is currently off.

    Attributes:
        name: The tool's name.
        since: When it was switched off, as a Unix timestamp.
        reason: Why, in the user's words, when they gave one.
    """

    name: str
    since: float
    reason: str = ""


class ToolStateStore:
    """Which tools are off, in SQLite, next to everything else that persists."""

    _DDL = """
        CREATE TABLE IF NOT EXISTS tool_state (
            name         TEXT PRIMARY KEY,
            disabled_at  REAL NOT NULL,
            reason       TEXT NOT NULL DEFAULT ''
        );
    """

    def __init__(self, db_path: Path) -> None:
        """Bind the store to a database file, without opening it yet.

        Args:
            db_path: The shared SQLite file.
        """
        self._db_path = db_path
        self._db: aiosqlite_t.Connection | None = None
        self._lock = asyncio.Lock()

    async def open(self) -> None:
        """Connect and make sure the table exists."""
        import aiosqlite

        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self._db_path)
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.executescript(self._DDL)
        await self._db.commit()

    async def close(self) -> None:
        """Release the connection."""
        if self._db is not None:
            await self._db.close()
            self._db = None

    def _require_open(self) -> aiosqlite_t.Connection:
        """Return the connection, or say plainly that nobody opened it."""
        if self._db is None:
            raise RuntimeError("call open() on the tool state store before using it")
        return self._db

    async def disabled(self) -> frozenset[str]:
        """Satisfy :class:`core.router.ToolGate`: the names currently off.

        Asks only for the names. This is the one hot path in the module -- it
        runs on every tool round of every turn -- and building dataclasses only
        to throw away two of their three fields is work nobody wanted.
        """
        async with self._lock:
            db = self._require_open()
            cursor = await db.execute("SELECT name FROM tool_state")
            rows = await cursor.fetchall()
        return frozenset(row[0] for row in rows)

    async def switched_off(self) -> list[SwitchedOff]:
        """Everything currently off, with when and why, oldest first."""
        async with self._lock:
            db = self._require_open()
            cursor = await db.execute(
                "SELECT name, disabled_at, reason FROM tool_state ORDER BY disabled_at"
            )
            rows = await cursor.fetchall()
        return [SwitchedOff(name=r[0], since=r[1], reason=r[2]) for r in rows]

    async def disabled_since(self, name: str) -> float | None:
        """When a tool was switched off, or ``None`` if it is not.

        The second stage reads this. Written and never read, the timestamp
        would have been a column that only looked like a safeguard.
        """
        async with self._lock:
            db = self._require_open()
            cursor = await db.execute("SELECT disabled_at FROM tool_state WHERE name = ?", (name,))
            row = await cursor.fetchone()
        return None if row is None else float(row[0])

    async def is_disabled(self, name: str) -> bool:
        """Whether one tool is currently off."""
        return await self.disabled_since(name) is not None

    async def disable(self, name: str, reason: str = "") -> bool:
        """Switch a tool off.

        Refuses a protected name here as well as in the tool that calls it.
        The constant lives in this module, so a future caller that does not
        know about it should still not be able to route around it -- a guard
        enforced in only one of two places is a guard with a door beside it.

        Args:
            name: The tool.
            reason: Why, in the user's words.

        Returns:
            ``True`` when this call turned it off; ``False`` when it already
            was, or when the name is protected.
        """
        if name in PROTECTED:
            return False

        async with self._lock:
            db = self._require_open()
            cursor = await db.execute(
                "INSERT OR IGNORE INTO tool_state(name, disabled_at, reason) VALUES(?, ?, ?)",
                (name, time.time(), reason.strip()),
            )
            await db.commit()
            return cursor.rowcount > 0

    async def enable(self, name: str) -> bool:
        """Switch a tool back on.

        Deliberately validates nothing. A row can outlive the tool it names --
        after the removal job is done, or after a rename -- and this is the
        only way to clear one from the chat.

        Returns:
            ``True`` when it was off and is now on.
        """
        async with self._lock:
            db = self._require_open()
            cursor = await db.execute("DELETE FROM tool_state WHERE name = ?", (name,))
            await db.commit()
            return cursor.rowcount > 0


__all__ = [
    "MIN_TIME_OFF_SECONDS",
    "PROTECTED",
    "SwitchedOff",
    "ToolStateStore",
]
