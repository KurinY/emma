"""Which tools are switched off, and the two-stage road to removing one.

Removing a tool means editing the composition root and deploying: a development
job, and not one that can be undone in a hurry. Switching it off is reversible,
takes effect on the next turn and survives a restart, which makes it the right
first move -- and, once it has been off for a while without being missed, the
evidence that removing it was really wanted.

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
PROTECTED = frozenset({"list_tools", "enable_tool"})


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
        """Satisfy :class:`core.router.ToolGate`: the names currently off."""
        return frozenset(entry.name for entry in await self.switched_off())

    async def switched_off(self) -> list[SwitchedOff]:
        """Everything currently off, with when and why, oldest first."""
        async with self._lock:
            db = self._require_open()
            cursor = await db.execute(
                "SELECT name, disabled_at, reason FROM tool_state ORDER BY disabled_at"
            )
            rows = await cursor.fetchall()
        return [SwitchedOff(name=r[0], since=r[1], reason=r[2]) for r in rows]

    async def is_disabled(self, name: str) -> bool:
        """Whether one tool is currently off."""
        async with self._lock:
            db = self._require_open()
            cursor = await db.execute("SELECT 1 FROM tool_state WHERE name = ?", (name,))
            return await cursor.fetchone() is not None

    async def disable(self, name: str, reason: str = "") -> bool:
        """Switch a tool off.

        Args:
            name: The tool.
            reason: Why, in the user's words.

        Returns:
            ``True`` when this call turned it off, ``False`` when it already was.
        """
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

        Returns:
            ``True`` when it was off and is now on.
        """
        async with self._lock:
            db = self._require_open()
            cursor = await db.execute("DELETE FROM tool_state WHERE name = ?", (name,))
            await db.commit()
            return cursor.rowcount > 0


__all__ = ["PROTECTED", "SwitchedOff", "ToolStateStore"]
