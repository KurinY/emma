"""The orchestrator: context in, answer out.

:class:`Router` is the heart of the assistant and the one component that is
deliberately channel-agnostic.  It receives an :class:`AssistantRequest` -- a
plain object with the text, the user and the conversation -- and returns an
:class:`AssistantResponse`.  Nothing in this module knows that Telegram exists,
which is exactly what lets a future voice satellite reuse it untouched.

The turn is already shaped as an *agentic loop*:

    1. build the request context from memory plus the new message;
    2. ask the model;
    3. if the model asked for tools, run them, feed the results back and go to
       step 2;
    4. otherwise return the prose and store the turn.

Version 1 registers no tool at all, so the loop exits after one iteration --
but the machinery, the declarations and the result plumbing are already in
place, and registering a tool later requires no change to this file.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from core.llm import LanguageModel, LLMUnavailableError, Message
from core.memory import ConversationMemory, StoredMessage

logger = logging.getLogger(__name__)

#: Shown to the user when the model cannot be reached.  In Italian, because it
#: is the only string in the code base the user actually reads.
FALLBACK_UNAVAILABLE = "Non riesco a contattare il cervello in questo momento, riprova tra poco."

#: Shown when the model answers without any prose -- rare, but possible when a
#: turn ends on a tool block alone.
FALLBACK_EMPTY = "Non ho una risposta da darti su questo, riprova a chiedermelo."

#: Shown when the tool loop hits its ceiling, which would otherwise mean an
#: unbounded (and billable) sequence of calls.
FALLBACK_TOO_MANY_STEPS = (
    "Mi sto perdendo in troppi passaggi per questa richiesta, riformulala pure."
)

#: Maximum number of tool rounds inside a single turn.
DEFAULT_MAX_TOOL_ITERATIONS = 5


@dataclass(frozen=True, slots=True)
class AssistantRequest:
    """A message coming from any channel, in the internal canonical form.

    Attributes:
        text: What the user wrote, already stripped of channel decorations.
        user_id: Stable identifier of the person, as seen by the channel.
        conversation_id: Identifier of the thread this message belongs to.
            Messages sharing it share a memory window.
    """

    text: str
    user_id: str
    conversation_id: str


@dataclass(frozen=True, slots=True)
class AssistantResponse:
    """The assistant reply, in the internal canonical form.

    Attributes:
        text: Plain text to deliver back through the originating channel.
        degraded: ``True`` when the text is a fallback rather than a genuine
            model answer.  Channels may use it to adjust presentation; the
            Telegram adapter simply sends the text either way.
    """

    text: str
    degraded: bool = False


@runtime_checkable
class Tool(Protocol):
    """A capability the model may invoke during a turn.

    Implementations are registered on the router at construction time and are
    never imported by it, so adding one touches no code in this module.
    """

    #: Name the model uses to call the tool.  Must be unique in a router.
    name: str

    #: One-line description; this is what the model reads to decide when to
    #: call the tool, so it is part of the prompt, not just documentation.
    description: str

    #: JSON Schema of the accepted arguments.
    input_schema: dict[str, Any]

    async def run(self, arguments: dict[str, Any]) -> str:
        """Execute the tool and return its result as text.

        Args:
            arguments: Arguments produced by the model, already validated
                against :attr:`input_schema` by the API.

        Returns:
            The result, as text to hand back to the model.
        """
        ...


@dataclass(slots=True)
class Router:
    """Turns an :class:`AssistantRequest` into an :class:`AssistantResponse`.

    Attributes:
        llm: The language model to consult.
        memory: Where conversation history is read from and written to.
        system_prompt: The assistant personality.
        tools: Tools the model may call.  Empty in version 1.
        max_tool_iterations: Ceiling on the tool rounds of a single turn.
    """

    llm: LanguageModel
    memory: ConversationMemory
    system_prompt: str
    tools: Sequence[Tool] = ()
    max_tool_iterations: int = DEFAULT_MAX_TOOL_ITERATIONS
    _tools_by_name: dict[str, Tool] = field(init=False, repr=False, default_factory=dict)

    def __post_init__(self) -> None:
        """Index the tools by name and reject duplicate registrations.

        Raises:
            ValueError: If two tools share the same name, which would make the
                model's choice ambiguous.
        """
        for tool in self.tools:
            if tool.name in self._tools_by_name:
                raise ValueError(f"duplicate tool name: {tool.name}")
            self._tools_by_name[tool.name] = tool

    async def handle(self, request: AssistantRequest) -> AssistantResponse:
        """Run one full turn.

        This coroutine never raises because of the model or of a tool: every
        failure is turned into a polite, degraded answer, so the process stays
        alive and the user is never left without a reply.

        Args:
            request: The incoming message.

        Returns:
            The reply to send back.
        """
        history = await self.memory.get_history(request.conversation_id)
        messages: list[Message] = [{"role": item.role, "content": item.content} for item in history]
        messages.append({"role": "user", "content": request.text})

        try:
            answer = await self._run_agentic_loop(messages)
        except LLMUnavailableError:
            logger.error(
                "conversation=%s: giving up on this turn, model unreachable",
                request.conversation_id,
            )
            return AssistantResponse(text=FALLBACK_UNAVAILABLE, degraded=True)

        if answer.degraded:
            # A degraded turn is not worth remembering: storing it would poison
            # the window with an apology the model would then try to explain.
            return answer

        await self.memory.append(
            request.conversation_id, StoredMessage(role="user", content=request.text)
        )
        await self.memory.append(
            request.conversation_id,
            StoredMessage(role="assistant", content=answer.text),
        )
        return answer

    async def _run_agentic_loop(self, messages: list[Message]) -> AssistantResponse:
        """Consult the model, running any tool it asks for, until it is done.

        Args:
            messages: The request context; extended in place with the
                intermediate assistant and tool-result messages of the turn.

        Returns:
            The final answer of the turn.

        Raises:
            LLMUnavailableError: Propagated from the model client so that
                :meth:`handle` can turn it into a polite message.
        """
        tool_schemas = self._tool_schemas()

        for iteration in range(1, self.max_tool_iterations + 1):
            reply = await self.llm.complete(
                system=self.system_prompt,
                messages=messages,
                tools=tool_schemas,
            )

            tool_uses = reply.tool_uses
            if reply.stop_reason != "tool_use" or not tool_uses:
                text = reply.text
                if not text:
                    logger.warning("model returned no text (stop_reason=%s)", reply.stop_reason)
                    return AssistantResponse(text=FALLBACK_EMPTY, degraded=True)
                return AssistantResponse(text=text)

            # Replay the assistant turn verbatim, then answer every tool_use
            # with a tool_result carrying the same id, in one user message.
            messages.append(reply.to_assistant_message())
            results: list[dict[str, Any]] = []
            for call in tool_uses:
                logger.info("tool round %d: running %s(%s)", iteration, call.name, call.input)
                results.append(await self._execute_tool(call.id, call.name, call.input))
            messages.append({"role": "user", "content": results})

        logger.warning("tool loop hit its ceiling of %d rounds", self.max_tool_iterations)
        return AssistantResponse(text=FALLBACK_TOO_MANY_STEPS, degraded=True)

    async def _execute_tool(
        self, call_id: str, name: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        """Run one tool and wrap its outcome in a ``tool_result`` block.

        A tool that raises is reported back to the model as an error rather
        than propagated: the model is usually able to recover, and a buggy tool
        must never take the assistant down.

        Args:
            call_id: The ``tool_use`` id this result answers.
            name: Name of the requested tool.
            arguments: Arguments produced by the model.

        Returns:
            A ``tool_result`` content block.
        """
        tool = self._tools_by_name.get(name)
        if tool is None:
            logger.error("model asked for unknown tool '%s'", name)
            return _tool_result(call_id, f"unknown tool: {name}", is_error=True)

        try:
            output = await tool.run(arguments)
        except Exception as exc:  # a tool must never be able to crash the turn
            logger.exception("tool '%s' failed", name)
            return _tool_result(call_id, f"tool error: {exc}", is_error=True)
        return _tool_result(call_id, output)

    def _tool_schemas(self) -> list[dict[str, Any]] | None:
        """Return the tool declarations for the API, or ``None`` if there are none."""
        if not self.tools:
            return None
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.input_schema,
            }
            for tool in self.tools
        ]


def _tool_result(call_id: str, content: str, *, is_error: bool = False) -> dict[str, Any]:
    """Build a ``tool_result`` content block.

    Args:
        call_id: The ``tool_use`` id being answered.
        content: Text handed back to the model.
        is_error: Whether the tool failed.

    Returns:
        The content block, ready to be put in a user message.
    """
    block: dict[str, Any] = {
        "type": "tool_result",
        "tool_use_id": call_id,
        "content": content,
    }
    if is_error:
        block["is_error"] = True
    return block
