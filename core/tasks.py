"""Development task queue.

EMMA cannot change her own code -- she is the process that is running -- but she
can record that a change is wanted, and report on it afterwards.  This module is
the queue that makes that possible: she writes requests into it, a developer
reads them, and the questions that come back travel the same way.

The arrangement, and the reasoning behind it, is entry 17 of ``REVISIONE.md``.
Two properties of it are visible in the code here:

* **She never speaks first.**  Nothing in this module notifies anybody.  The
  developer leaves a question in :attr:`Task.note`; the user sees it only when
  they ask EMMA for the state of play.  The answer travels back the same way.
* **The user's judgement lands between the stages, not only at the end.**  A
  task advances one :class:`Stage` at a time, and each advance parks it in
  ``waiting_user`` until an answer arrives.

The queue lives in the same SQLite file as the conversation history.  That is
deliberate: the integrity check, the snapshots and the consistent backup built
around that file then cover the tasks too, where a second database would have
been quietly unprotected.  WAL mode makes the concurrent access safe.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    import aiosqlite as aiosqlite_t

#: How far a task has got.  The value records what has been *done*; when the
#: status is ``waiting_user`` the note asks permission for the step after it.
Stage = Literal["new", "understood", "implemented", "committed", "pushed", "deployed"]

#: Whose move it is.
#:
#: ``queued``        the developer's -- either untouched, or answered and ready
#: ``waiting_user``  the user's -- a question is sitting in ``note``
#: ``done``          finished, deployed
#: ``abandoned``     dropped on purpose; kept so it does not come back
Status = Literal["queued", "waiting_user", "done", "abandoned"]

#: The stages a task passes through, in order.  Used to describe progress, and
#: to name the step a checkpoint is asking permission for.
STAGE_ORDER: tuple[Stage, ...] = (
    "new",
    "understood",
    "implemented",
    "committed",
    "pushed",
    "deployed",
)


@dataclass(frozen=True, slots=True)
class Task:
    """One commissioned piece of work.

    Attributes:
        id: Identifier, and the number the user refers to it by.
        request: What was asked, in the user's own words.  Deliberately not
            summarised: the developer reads the original, not an interpretation
            of it made by a model that cannot see the code.
        stage: How far it has got.
        status: Whose move it is.
        note: What the developer has to say at this point -- typically the
            question the user is being asked.
        answer: The user's reply to that question, as EMMA recorded it.
        created_at: Unix timestamp of the request.
        updated_at: Unix timestamp of the last change.
    """

    id: int
    request: str
    stage: Stage
    status: Status
    note: str
    answer: str
    created_at: float
    updated_at: float


class TaskStore:
    """Persistent queue of development requests, backed by SQLite.

    Open it once at start-up and close it on shutdown, exactly like
    :class:`core.memory.SqliteConversationMemory`; the FastAPI lifespan does
    both.  It keeps its own connection rather than borrowing the memory's: the
    two have different responsibilities and only happen to share a file.
    """

    _DDL = """
        CREATE TABLE IF NOT EXISTS tasks (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            request    TEXT NOT NULL,
            stage      TEXT NOT NULL,
            status     TEXT NOT NULL,
            note       TEXT NOT NULL DEFAULT '',
            answer     TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks (status, id);

        -- One row, always id 1: when the developer's session last looked at the
        -- queue.  Without a service that restarts by itself, a session that
        -- dies takes the whole mechanism with it silently; this is how the user
        -- finds out, by asking EMMA and hearing "last seen two days ago".
        CREATE TABLE IF NOT EXISTS dev_heartbeat (
            id        INTEGER PRIMARY KEY CHECK (id = 1),
            last_seen REAL NOT NULL
        );
    """

    def __init__(self, db_path: Path) -> None:
        """Prepare the store.  The database file is not opened yet.

        Args:
            db_path: Path of the SQLite file, shared with the conversation
                history.  Its directory is created by whoever opens the memory
                first; this store does not create it.
        """
        self._db_path = db_path
        self._db: aiosqlite_t.Connection | None = None
        self._lock = asyncio.Lock()

    async def open(self) -> None:
        """Open the connection and create the tables if they are not there."""
        import aiosqlite

        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self._db_path)
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.executescript(self._DDL)
        await self._db.commit()

    async def close(self) -> None:
        """Flush pending writes and release the connection."""
        if self._db is not None:
            await self._db.close()
            self._db = None

    # ----------------------------------------------------------------- #
    # What EMMA does
    # ----------------------------------------------------------------- #

    async def create(self, request: str) -> int:
        """Record a new request and return the number it will be known by.

        Args:
            request: The user's words.  Stored verbatim.

        Returns:
            The identifier of the new task.

        Raises:
            ValueError: If the request is blank -- a task nobody can read is
                worse than no task, because it still asks to be answered.
        """
        text = request.strip()
        if not text:
            raise ValueError("a development request cannot be empty")

        async with self._lock:
            db = self._require_open()
            now = time.time()
            cursor = await db.execute(
                "INSERT INTO tasks(created_at, updated_at, request, stage, status) "
                "VALUES(?,?,?,'new','queued')",
                (now, now, text),
            )
            await db.commit()
            return int(cursor.lastrowid or 0)

    async def awaiting_user(self) -> list[Task]:
        """Return the tasks that are waiting for the user, oldest first."""
        return await self._select("WHERE status = 'waiting_user' ORDER BY id")

    async def open_tasks(self) -> list[Task]:
        """Return everything not finished or dropped, oldest first."""
        return await self._select("WHERE status IN ('queued','waiting_user') ORDER BY id")

    async def record_answer(self, task_id: int, answer: str) -> bool:
        """Store the user's reply and hand the task back to the developer.

        Args:
            task_id: Which task the answer belongs to.
            answer: What the user said, as EMMA understood it.

        Returns:
            ``True`` if the answer was recorded.  ``False`` when the task does
            not exist or was not waiting for one -- answering a question nobody
            asked is a mistake worth reporting rather than absorbing.
        """
        text = answer.strip()
        if not text:
            return False

        async with self._lock:
            db = self._require_open()
            cursor = await db.execute(
                "UPDATE tasks SET answer = ?, status = 'queued', updated_at = ? "
                "WHERE id = ? AND status = 'waiting_user'",
                (text, time.time(), task_id),
            )
            await db.commit()
            return cursor.rowcount > 0

    async def last_seen(self) -> float | None:
        """When the developer's session last looked at the queue.

        Returns:
            A Unix timestamp, or ``None`` if it has never looked -- which on a
            queue that has tasks in it means the session is not running.
        """
        async with self._lock:
            db = self._require_open()
            cursor = await db.execute("SELECT last_seen FROM dev_heartbeat WHERE id = 1")
            row = await cursor.fetchone()
            return float(row[0]) if row else None

    # ----------------------------------------------------------------- #
    # What the developer's session does
    # ----------------------------------------------------------------- #

    async def queued(self) -> list[Task]:
        """Return the tasks waiting for the developer, oldest first."""
        return await self._select("WHERE status = 'queued' ORDER BY id")

    async def advance(self, task_id: int, stage: Stage, note: str) -> bool:
        """Move a task to ``stage`` and park it on the user with a question.

        This is a checkpoint: the stage records what has just been finished,
        and the note asks permission for the step after it.

        Args:
            task_id: Which task.
            stage: What has now been done.
            note: What to tell the user, ending in the question.

        Returns:
            ``True`` if the task existed and was updated.

        Raises:
            ValueError: If ``stage`` is not one of :data:`STAGE_ORDER`.
        """
        if stage not in STAGE_ORDER:
            raise ValueError(f"unknown stage '{stage}'")

        async with self._lock:
            db = self._require_open()
            cursor = await db.execute(
                "UPDATE tasks SET stage = ?, note = ?, answer = '', "
                "status = 'waiting_user', updated_at = ? WHERE id = ?",
                (stage, note.strip(), time.time(), task_id),
            )
            await db.commit()
            return cursor.rowcount > 0

    async def finish(self, task_id: int, note: str = "") -> bool:
        """Close a task that has been deployed.

        Args:
            task_id: Which task.
            note: Optional closing remark for the user.

        Returns:
            ``True`` if the task existed and was updated.
        """
        return await self._close_with(task_id, "done", "deployed", note)

    async def abandon(self, task_id: int, note: str = "") -> bool:
        """Drop a task on purpose, so it stops asking to be dealt with.

        Args:
            task_id: Which task.
            note: Why it was dropped.

        Returns:
            ``True`` if the task existed and was updated.
        """
        async with self._lock:
            db = self._require_open()
            cursor = await db.execute(
                "UPDATE tasks SET status = 'abandoned', note = ?, updated_at = ? WHERE id = ?",
                (note.strip(), time.time(), task_id),
            )
            await db.commit()
            return cursor.rowcount > 0

    async def touch(self) -> None:
        """Record that the developer's session is alive and looking."""
        async with self._lock:
            db = self._require_open()
            await db.execute(
                "INSERT INTO dev_heartbeat(id, last_seen) VALUES(1, ?) "
                "ON CONFLICT(id) DO UPDATE SET last_seen = excluded.last_seen",
                (time.time(),),
            )
            await db.commit()

    # ----------------------------------------------------------------- #
    # Internals
    # ----------------------------------------------------------------- #

    def _require_open(self) -> aiosqlite_t.Connection:
        """Return the connection, or say plainly that nobody opened it."""
        if self._db is None:
            raise RuntimeError("call open() on the task store before using it")
        return self._db

    async def _close_with(self, task_id: int, status: Status, stage: Stage, note: str) -> bool:
        """Set the final status and stage of a task."""
        async with self._lock:
            db = self._require_open()
            cursor = await db.execute(
                "UPDATE tasks SET status = ?, stage = ?, note = ?, updated_at = ? WHERE id = ?",
                (status, stage, note.strip(), time.time(), task_id),
            )
            await db.commit()
            return cursor.rowcount > 0

    async def _select(self, where: str) -> list[Task]:
        """Run a SELECT over the task table and build the rows."""
        async with self._lock:
            db = self._require_open()
            cursor = await db.execute(
                "SELECT id, request, stage, status, note, answer, created_at, updated_at "
                f"FROM tasks {where}"
            )
            rows = await cursor.fetchall()
            return [
                Task(
                    id=int(r[0]),
                    request=r[1],
                    stage=r[2],
                    status=r[3],
                    note=r[4],
                    answer=r[5],
                    created_at=float(r[6]),
                    updated_at=float(r[7]),
                )
                for r in rows
            ]
