"""Conversation memory.

The router never talks to a concrete storage backend: it depends on the
:class:`ConversationMemory` interface defined here.  Version 1 ships a single
implementation that keeps everything in RAM; a future version can swap in an
SQLite-backed one by implementing the same three coroutines, with no change to
``core/router.py``.

The stored unit is a :class:`StoredMessage`: a role (``user`` or ``assistant``)
plus plain text.  Intermediate tool-use blocks are deliberately *not* stored --
they belong to a single agentic turn, not to the conversation the user would
recognise if they scrolled up in Telegram.
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from collections import defaultdict
from dataclasses import dataclass
from typing import Literal

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
