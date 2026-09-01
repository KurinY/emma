"""Facts EMMA has been asked to keep, and the store that keeps them.

Not to be confused with :mod:`core.memory`, which holds the conversation and
forgets by age: it keeps the last twenty messages and deletes the rest, so a
fact told on Monday is gone by Wednesday however important it was. The
criterion there is recency, and recency is the wrong criterion for "my daughter
is called Sara".

This is the other half: a small set of things that do not expire, written only
when the user asks for them, and put in front of the model on every turn rather
than searched for. See REVISIONE.md, entry 18.

**Why everything is injected rather than retrieved.** Almost every published
memory system solves a retrieval problem -- which of thousands of memories
matter to this question -- and that problem comes from many users and unbounded
history. EMMA has one user. Measured on production logs, an exchange costs
about 2,360 input tokens and a fact costs about 15, so injecting a hundred
facts costs less than the retrieval machinery would, and nothing can be
retrieved wrongly because nothing is retrieved. The arithmetic stops working at
roughly 150 facts (2360 / 15), which is why :data:`MAX_ACTIVE_FACTS` exists and
sits below it.

The table lives in the same SQLite file as the conversation and the development
queue, for the reason recorded in entry 17: the integrity check, the snapshots
and the consistent backup were all built around that one file, and a second
file would need every one of them again.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    import aiosqlite as aiosqlite_t

#: How many facts may be active at once.
#:
#: Fifty, not the hundred first written here, and the difference was found by
#: measuring rather than reasoning. A hundred is unreachable in practice: a
#: typical fact is about fifty characters, so the four-thousand-character
#: ceiling on the injected context binds first, at roughly eighty. A limit that
#: promises more than it can deliver is worse than a smaller honest one -- the
#: facts beyond it are stored, counted, and never seen by the model.
#:
#: At fifty the two ceilings stop competing: the count binds for ordinary facts
#: and MAX_CONTEXT_CHARS goes back to being what it should be, a guard against
#: someone pasting a page. The cost is real and worth stating: measured against
#: production traffic, fifty facts take an exchange from about 2,360 input
#: tokens to about 3,360, which is roughly 59 exchanges a day against Groq's
#: 200,000 rather than 84.
MAX_ACTIVE_FACTS = 50

#: Longest single fact, in characters. A fact is a sentence, not a document:
#: anything longer is a conversation that belongs in the history instead, and
#: pasting a page in here would quietly double the cost of every later turn.
MAX_FACT_LENGTH = 300


@dataclass(frozen=True, slots=True)
class Fact:
    """One thing EMMA has been asked to remember.

    Attributes:
        id: Its number, which is how the user refers to it when forgetting.
        text: The fact itself, in the user's own words.
        created_at: When it was recorded, as a Unix timestamp.
        forgotten_at: When it was forgotten, or ``None`` while it is active.
    """

    id: int
    text: str
    created_at: float
    forgotten_at: float | None = None


class FactStore:
    """Facts in SQLite, in the same file as everything else that persists."""

    _DDL = """
        CREATE TABLE IF NOT EXISTS facts (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at   REAL NOT NULL,
            text         TEXT NOT NULL,
            forgotten_at REAL
        );
        -- Every read asks for the active ones in order, and only those.
        CREATE INDEX IF NOT EXISTS idx_facts_active
            ON facts (forgotten_at, id);
    """

    def __init__(self, db_path: Path) -> None:
        """Bind the store to a database file, without opening it yet.

        Args:
            db_path: The shared SQLite file.
        """
        self._db_path = db_path
        self._db: aiosqlite_t.Connection | None = None
        # The same lock discipline as the other two stores: a turn may read and
        # write, and the two must not interleave.
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
        """Return the connection, or say plainly that nobody opened it.

        Deliberately not an ``assert``: those vanish under ``python -O``, and
        what remains is an ``AttributeError`` on ``None`` three frames away
        from the mistake.
        """
        if self._db is None:
            raise RuntimeError("call open() on the fact store before using it")
        return self._db

    async def remember(self, text: str) -> tuple[int | None, str]:
        """Record a fact.

        Args:
            text: The fact, in the user's words.

        Returns:
            Its number and an empty reason on success; ``None`` and a reason
            when it was refused. Refusals are returned rather than raised
            because the caller is a tool, and a tool's job is to hand the model
            a sentence it can pass on.
        """
        cleaned = " ".join(text.split())
        if not cleaned:
            return None, "il fatto e' vuoto"
        if len(cleaned) > MAX_FACT_LENGTH:
            return None, (
                f"il fatto e' troppo lungo ({len(cleaned)} caratteri, "
                f"il massimo e' {MAX_FACT_LENGTH})"
            )

        async with self._lock:
            db = self._require_open()

            # The same fact twice is not two facts. Compared case-insensitively
            # because "Sara" and "sara" are the same thing to remember, and a
            # duplicate would be paid for on every turn from now on.
            cursor = await db.execute(
                "SELECT id FROM facts WHERE forgotten_at IS NULL AND lower(text) = lower(?)",
                (cleaned,),
            )
            if (existing := await cursor.fetchone()) is not None:
                return existing[0], "gia' registrato"

            cursor = await db.execute("SELECT COUNT(*) FROM facts WHERE forgotten_at IS NULL")
            row = await cursor.fetchone()
            if row is not None and row[0] >= MAX_ACTIVE_FACTS:
                return None, (
                    f"ci sono gia' {MAX_ACTIVE_FACTS} fatti attivi, che e' il "
                    f"massimo; dimenticane uno per farne posto"
                )

            cursor = await db.execute(
                "INSERT INTO facts(created_at, text) VALUES(?, ?)",
                (time.time(), cleaned),
            )
            await db.commit()
            return cursor.lastrowid, ""

    async def forget(self, fact_id: int) -> bool:
        """Stop using a fact, without destroying the record of it.

        The row stays, stamped with the moment it stopped counting. The same
        choice made for an abandoned development job and for a corrupt
        database, and for the same reason: a decision that can be read back is
        less final than one that cannot, and a fact forgotten by mistake is
        still there to be restored by hand.

        Args:
            fact_id: Which fact.

        Returns:
            ``True`` if it existed and was active.
        """
        async with self._lock:
            db = self._require_open()
            cursor = await db.execute(
                "UPDATE facts SET forgotten_at = ? WHERE id = ? AND forgotten_at IS NULL",
                (time.time(), fact_id),
            )
            await db.commit()
            return cursor.rowcount > 0

    async def active(self) -> list[Fact]:
        """Every fact currently in force, oldest first."""
        async with self._lock:
            db = self._require_open()
            cursor = await db.execute(
                "SELECT id, text, created_at, forgotten_at FROM facts "
                "WHERE forgotten_at IS NULL ORDER BY id"
            )
            rows = await cursor.fetchall()
        return [Fact(id=r[0], text=r[1], created_at=r[2], forgotten_at=r[3]) for r in rows]


__all__ = ["MAX_ACTIVE_FACTS", "MAX_FACT_LENGTH", "Fact", "FactStore"]
