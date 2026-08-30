"""Conversation memory.

The router never talks to a concrete storage backend: it depends on the
:class:`ConversationMemory` interface defined here.  Two implementations are
provided:

* :class:`InMemoryConversationMemory` -- fast, zero-dependency, lost on restart.
* :class:`SqliteConversationMemory` -- persistent across restarts, backed by an
  SQLite file via ``aiosqlite``.

The stored unit is a :class:`StoredMessage`: a role (``user`` or ``assistant``)
plus plain text.  Intermediate tool-use blocks are deliberately *not* stored --
they belong to a single agentic turn, not to the conversation the user would
recognise if they scrolled up in Telegram.
"""

from __future__ import annotations

import asyncio
import time
from abc import ABC, abstractmethod
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    import aiosqlite as aiosqlite_t

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

    @property
    def max_messages(self) -> int:
        """Maximum number of messages retained per conversation."""
        return self._max_messages

    async def open(self) -> None:
        """Open the database connection and create the schema if needed."""
        import aiosqlite

        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self._db_path)
        await self._db.executescript(self._DDL)
        await self._db.commit()

    async def close(self) -> None:
        """Flush pending writes and close the connection."""
        if self._db is not None:
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
