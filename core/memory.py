"""Conversation memory.

The router never talks to a concrete storage backend: it depends on the
:class:`ConversationMemory` interface defined here.  Two implementations are
provided:

* :class:`InMemoryConversationMemory` -- fast, zero-dependency, lost on restart.
* :class:`SqliteConversationMemory` -- persistent across restarts, backed by an
  SQLite file via ``aiosqlite``, with an integrity check and snapshot-based
  recovery on open.

The stored unit is a :class:`StoredMessage`: a role (``user`` or ``assistant``)
plus plain text.  Intermediate tool-use blocks are deliberately *not* stored --
they belong to a single agentic turn, not to the conversation the user would
recognise if they scrolled up in Telegram.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import time
from abc import ABC, abstractmethod
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    import aiosqlite as aiosqlite_t

logger = logging.getLogger(__name__)

#: Roles accepted by the Anthropic Messages API for conversation history.
Role = Literal["user", "assistant"]


@dataclass(frozen=True, slots=True)
class StoredMessage:
    """A single turn kept in the conversation history.

    Attributes:
        role: Who produced the text, ``"user"`` or ``"assistant"``.
        content: The text itself.
    """

    role: Role
    content: str


class ConversationMemory(ABC):
    """Storage interface for per-conversation history.

    Implementations must be safe to call from a single asyncio event loop and
    must keep conversations isolated from one another: two different
    ``conversation_id`` values never see each other's messages.
    """

    @abstractmethod
    async def get_history(self, conversation_id: str) -> list[StoredMessage]:
        """Return the stored messages of a conversation, oldest first.

        Args:
            conversation_id: Opaque identifier of the conversation.

        Returns:
            A new list -- callers may mutate it without affecting storage.
            Unknown conversations yield an empty list rather than an error.
        """

    @abstractmethod
    async def append(self, conversation_id: str, message: StoredMessage) -> None:
        """Add one message to the end of a conversation.

        Args:
            conversation_id: Opaque identifier of the conversation.
            message: The message to store.
        """

    @abstractmethod
    async def prune(self, conversation_id: str) -> None:
        """Drop whatever exceeds the retention policy of the implementation.

        Called by :meth:`append` implementations and, occasionally, explicitly.
        It must be idempotent: pruning twice in a row changes nothing.

        Args:
            conversation_id: Opaque identifier of the conversation.
        """


class InMemoryConversationMemory(ConversationMemory):
    """In-process history with a fixed-size sliding window.

    Everything lives in a dictionary, so history is lost when the process
    restarts.  That is an accepted limitation of version 1 and the reason the
    interface above exists.

    The window is applied on whole messages and, when the cut would leave an
    orphan ``assistant`` message at the front, one extra message is dropped:
    the Anthropic Messages API requires the first message of a request to come
    from the user.
    """

    def __init__(self, max_messages: int) -> None:
        """Create an empty store.

        Args:
            max_messages: Maximum number of messages kept per conversation.

        Raises:
            ValueError: If ``max_messages`` is not strictly positive.
        """
        if max_messages <= 0:
            raise ValueError("max_messages must be greater than zero")
        self._max_messages = max_messages
        self._conversations: defaultdict[str, list[StoredMessage]] = defaultdict(list)
        # Guards the read-modify-write sequences below.  Handlers for two
        # Telegram updates can interleave at any await point, and PTB does run
        # them concurrently.
        self._lock = asyncio.Lock()

    @property
    def max_messages(self) -> int:
        """Maximum number of messages retained per conversation."""
        return self._max_messages

    async def get_history(self, conversation_id: str) -> list[StoredMessage]:
        """Return a copy of the stored messages, oldest first."""
        async with self._lock:
            return list(self._conversations.get(conversation_id, ()))

    async def append(self, conversation_id: str, message: StoredMessage) -> None:
        """Append ``message`` and immediately re-apply the sliding window."""
        async with self._lock:
            self._conversations[conversation_id].append(message)
            self._prune_locked(conversation_id)

    async def prune(self, conversation_id: str) -> None:
        """Re-apply the sliding window to a conversation."""
        async with self._lock:
            self._prune_locked(conversation_id)

    def _prune_locked(self, conversation_id: str) -> None:
        """Trim one conversation.  The caller must already hold the lock."""
        messages = self._conversations.get(conversation_id)
        if not messages:
            return
        if len(messages) > self._max_messages:
            del messages[: len(messages) - self._max_messages]
        # Never start the window on an assistant message: the API rejects it.
        while messages and messages[0].role != "user":
            del messages[0]


class SqliteConversationMemory(ConversationMemory):
    """Persistent history stored in an SQLite file via ``aiosqlite``.

    History survives process restarts.  The sliding window and the
    "never start on an assistant message" invariant are enforced on every
    write, just like :class:`InMemoryConversationMemory`.

    Call :meth:`open` once before the first use (e.g. in the FastAPI lifespan)
    and :meth:`close` on shutdown to flush and release the connection.

    **Self-healing.**  :meth:`open` runs ``PRAGMA integrity_check`` before
    handing the database to the application.  If the check fails, the damaged
    file is moved aside -- never deleted -- and the newest healthy snapshot is
    put in its place; if no snapshot survives, the store starts empty.  Every
    step is logged at ``ERROR`` level, because a silent recovery is a recovery
    you cannot audit.

    Recovery happens **only** on verified corruption.  A service that fails to
    start for any other reason -- a bad configuration, a missing dependency --
    reaches none of this code, which is deliberate: restoring a database
    because something unrelated broke would discard good history without
    fixing the actual fault.

    Snapshots are written with ``VACUUM INTO`` (a consistent copy of a live
    database) on every successful open and on every clean shutdown, and are
    verified before they replace the previous generation.  Two generations are
    kept, so a snapshot taken moments before a fault still leaves an older one
    behind it.
    """

    _DDL = """
        CREATE TABLE IF NOT EXISTS messages (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            conv_id TEXT    NOT NULL,
            role    TEXT    NOT NULL,
            content TEXT    NOT NULL,
            ts      REAL    NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_conv_id ON messages (conv_id, id);
    """

    def __init__(self, db_path: Path, max_messages: int) -> None:
        """Prepare the store.  The database file is not opened yet.

        Args:
            db_path: Path of the SQLite file; created automatically on
                :meth:`open` if it does not exist yet.
            max_messages: Maximum messages kept per conversation.

        Raises:
            ValueError: If ``max_messages`` is not strictly positive.
        """
        if max_messages <= 0:
            raise ValueError("max_messages must be greater than zero")
        self._db_path = db_path
        self._max_messages = max_messages
        self._db: aiosqlite_t.Connection | None = None
        self._lock = asyncio.Lock()
        # Snapshots live beside the database: one current, one previous.
        self._snapshot = db_path.with_name(db_path.name + ".snapshot")
        self._snapshot_prev = db_path.with_name(db_path.name + ".snapshot.prev")

    @property
    def max_messages(self) -> int:
        """Maximum number of messages retained per conversation."""
        return self._max_messages

    async def open(self) -> None:
        """Open the database, recovering from corruption if necessary.

        The sequence is: verify the existing file, recover it if the check
        fails, connect, enable write-ahead logging, ensure the schema exists,
        and record a snapshot of the resulting -- known good -- state.
        """
        import aiosqlite

        try:
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            # Under systemd this is almost always ProtectSystem=strict without a
            # matching ReadWritePaths, and a bare traceback names neither.
            raise RuntimeError(
                f"cannot create the database directory {self._db_path.parent}: {exc}. "
                f"Under systemd, check that ReadWritePaths in emma.service covers it."
            ) from exc

        if self._db_path.exists() and not await self._file_is_healthy(self._db_path):
            await self._recover()

        self._db = await aiosqlite.connect(self._db_path)
        # WAL survives an abrupt kill far better than the rollback journal,
        # which is the failure this whole mechanism exists to contain.
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.executescript(self._DDL)
        await self._db.commit()

        await self._write_snapshot()

    async def close(self) -> None:
        """Snapshot the current state, then flush and close the connection."""
        if self._db is not None:
            await self._write_snapshot()
            await self._db.close()
            self._db = None

    async def get_history(self, conversation_id: str) -> list[StoredMessage]:
        """Return stored messages for the conversation, oldest first."""
        async with self._lock:
            assert self._db is not None, "call open() before using the store"
            cursor = await self._db.execute(
                "SELECT role, content FROM messages WHERE conv_id = ? ORDER BY id",
                (conversation_id,),
            )
            rows = await cursor.fetchall()
            return [StoredMessage(role=r, content=c) for r, c in rows]

    async def append(self, conversation_id: str, message: StoredMessage) -> None:
        """Persist ``message`` and re-apply the sliding window."""
        async with self._lock:
            assert self._db is not None, "call open() before using the store"
            await self._db.execute(
                "INSERT INTO messages(conv_id, role, content, ts) VALUES(?,?,?,?)",
                (conversation_id, message.role, message.content, time.time()),
            )
            await self._db.commit()
            await self._prune_locked(conversation_id)

    async def prune(self, conversation_id: str) -> None:
        """Re-apply the sliding window to a stored conversation."""
        async with self._lock:
            assert self._db is not None, "call open() before using the store"
            await self._prune_locked(conversation_id)

    async def _prune_locked(self, conversation_id: str) -> None:
        """Trim one conversation.  The caller must already hold the lock."""
        assert self._db is not None

        cur = await self._db.execute(
            "SELECT COUNT(*) FROM messages WHERE conv_id = ?", (conversation_id,)
        )
        (count,) = await cur.fetchone()

        excess = count - self._max_messages
        if excess > 0:
            cur = await self._db.execute(
                "SELECT id FROM messages WHERE conv_id = ? ORDER BY id LIMIT ?",
                (conversation_id, excess),
            )
            ids = [row[0] for row in await cur.fetchall()]
            if ids:
                placeholders = ",".join("?" * len(ids))
                await self._db.execute(f"DELETE FROM messages WHERE id IN ({placeholders})", ids)

        # Never start on an assistant message.
        cur = await self._db.execute(
            "SELECT id, role FROM messages WHERE conv_id = ? ORDER BY id LIMIT 1",
            (conversation_id,),
        )
        row = await cur.fetchone()
        while row and row[1] != "user":
            await self._db.execute("DELETE FROM messages WHERE id = ?", (row[0],))
            cur = await self._db.execute(
                "SELECT id, role FROM messages WHERE conv_id = ? ORDER BY id LIMIT 1",
                (conversation_id,),
            )
            row = await cur.fetchone()

        await self._db.commit()

    # ----------------------------------------------------------------- #
    # Integrity and recovery
    # ----------------------------------------------------------------- #

    @staticmethod
    async def _file_is_healthy(path: Path) -> bool:
        """Report whether ``path`` is an SQLite database that passes its own check.

        Args:
            path: File to inspect.  It is opened read-only and left untouched.

        Returns:
            ``True`` when ``PRAGMA integrity_check`` answers ``ok``.  Anything
            else -- a failed check, a file that is not a database at all, an
            unreadable file -- is ``False``.  This never raises: a health check
            that explodes would defeat its purpose.
        """
        import aiosqlite

        try:
            db = await aiosqlite.connect(path)
            try:
                cursor = await db.execute("PRAGMA integrity_check")
                row = await cursor.fetchone()
            finally:
                await db.close()
        except Exception as exc:  # any failure at all means "not healthy"
            logger.warning("integrity check could not read %s: %s", path, exc)
            return False
        return bool(row) and row[0] == "ok"

    def _sidecars(self, path: Path) -> tuple[Path, Path]:
        """Return the ``-wal`` and ``-shm`` companions of an SQLite file."""
        return (
            path.with_name(path.name + "-wal"),
            path.with_name(path.name + "-shm"),
        )

    async def _recover(self) -> None:
        """Quarantine a corrupt database and restore the newest healthy snapshot.

        Called only after :meth:`_file_is_healthy` has already failed on the
        live file, so the diagnosis is established rather than guessed.  The
        damaged file is renamed, never removed: whatever can still be salvaged
        from it stays on disk for as long as the user wants it.
        """
        stamp = time.strftime("%Y%m%d-%H%M%S")
        quarantined = self._db_path.with_name(f"{self._db_path.name}.corrupt-{stamp}")

        logger.error(
            "database integrity check FAILED for %s - starting recovery",
            self._db_path,
        )
        try:
            self._db_path.rename(quarantined)
            # A stale write-ahead log must not be replayed onto the file we
            # are about to restore, so the companions follow the database.
            for sidecar in self._sidecars(self._db_path):
                if sidecar.exists():
                    sidecar.rename(sidecar.with_name(f"{sidecar.name}.corrupt-{stamp}"))
        except OSError as exc:
            logger.error("could not move the corrupt database aside: %s", exc)
            return

        logger.error("corrupt database kept for inspection at %s", quarantined)

        for candidate in (self._snapshot, self._snapshot_prev):
            if not candidate.exists():
                continue
            if not await self._file_is_healthy(candidate):
                logger.error("snapshot %s is unusable, trying the one before it", candidate)
                continue
            try:
                shutil.copy2(candidate, self._db_path)
            except OSError as exc:
                logger.error("could not restore snapshot %s: %s", candidate, exc)
                continue
            logger.error(
                "RECOVERED: history restored from %s (messages written after that "
                "snapshot are lost; the damaged file is at %s)",
                candidate,
                quarantined,
            )
            return

        logger.error(
            "no healthy snapshot available - starting with an empty history. "
            "The damaged file is at %s and can still be examined by hand.",
            quarantined,
        )

    async def _write_snapshot(self) -> None:
        """Record a verified snapshot of the live database.

        ``VACUUM INTO`` produces a consistent copy without stopping writers,
        which a plain file copy cannot promise.  The new copy is checked before
        it is allowed to displace the previous generation, so a snapshot is
        never replaced by something worse than itself.

        A failure here is logged and swallowed: not having a fresh snapshot is
        a degraded state, not a reason to refuse to run.
        """
        assert self._db is not None

        tmp = self._snapshot.with_name(self._snapshot.name + ".tmp")
        try:
            tmp.unlink(missing_ok=True)  # VACUUM INTO refuses an existing target
            await self._db.execute("VACUUM INTO ?", (str(tmp),))
        except Exception as exc:  # snapshotting must never be fatal
            logger.warning("could not write the database snapshot: %s", exc)
            tmp.unlink(missing_ok=True)
            return

        if not await self._file_is_healthy(tmp):
            logger.warning("the snapshot just written is unhealthy, discarding it")
            tmp.unlink(missing_ok=True)
            return

        try:
            if self._snapshot.exists():
                self._snapshot.replace(self._snapshot_prev)
            tmp.replace(self._snapshot)
        except OSError as exc:
            logger.warning("could not rotate the database snapshots: %s", exc)
            tmp.unlink(missing_ok=True)
