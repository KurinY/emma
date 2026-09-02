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
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from core.llm import (
    LanguageModel,
    LLMQuotaExceededError,
    LLMUnavailableError,
    Message,
)
from core.memory import ConversationMemory, StoredMessage

logger = logging.getLogger(__name__)

#: Shown to the user when the model cannot be reached.  In Italian, because it
#: is the only string in the code base the user actually reads.
FALLBACK_UNAVAILABLE = "Non riesco a contattare il cervello in questo momento, riprova tra poco."

#: Shown when the model answered, but refused: a rate limit or a spent quota.
#: Deliberately different from the message above, which invites a retry -- the
#: one thing that will not work here.  On 31 August 2026 the daily token quota
#: ran out and the user was told to try again shortly; they spent the evening
#: guessing why the assistant had gone quiet, while the log said exactly why.
FALLBACK_QUOTA = (
    "Ho raggiunto il limite di richieste verso il modello. "
    "Non e' un guasto: riprovare adesso non aiuta."
)


def _quota_message(retry_after: float | None) -> str:
    """Say when it is worth coming back, when the server told us."""
    if retry_after is None:
        return f"{FALLBACK_QUOTA} Riprova piu' tardi."
    if retry_after < 90:
        return f"{FALLBACK_QUOTA} Riprova fra circa {round(retry_after)} secondi."
    return f"{FALLBACK_QUOTA} Riprova fra circa {round(retry_after / 60)} minuti."


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
    #: Why it degraded, as a short stable slug, or ``None`` for a real answer.
    #: Carried on the response so one log line says both what happened and why,
    #: rather than leaving the two to be correlated by timestamp.
    reason: str | None = None


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


@runtime_checkable
class ContextProvider(Protocol):
    """A fact the assistant must have in front of it, not go and fetch.

    A tool is consulted only when the model decides to consult it, and that
    decision is the model's to get wrong: a weaker one gets it wrong more
    often, a different one differently.  For anything whose *current* value
    matters that is a poor place to keep the truth, because an answer given
    once is remembered, and remembered answers get repeated long after they
    stopped being true.

    Measured on the running assistant, asked about work whose state had
    changed: it repeated a stale answer word for word four times out of ten,
    and once in ten even from a clean history.  Instructing it not to only
    moves those numbers, and moves them again on the next model.

    A provider sidesteps the decision.  Whatever it returns is appended to the
    system prompt on every turn, so a stale memory is contradicted by something
    already on the page rather than by a lookup nobody performed.  Keep it to a
    line: it is paid for on every message and competes for attention with the
    personality itself.
    """

    async def snapshot(self) -> str:
        """Return the current state in one short line, or ``""`` for nothing."""
        ...


@runtime_checkable
class ToolGate(Protocol):
    """Says which registered tools are switched off at this moment.

    A tool is installed by editing the composition root and removed the same
    way, which is deliberate and stays true: this does not make the tool set
    mutable, it makes part of it *quiet*. The router still holds every tool it
    was given; a gated one is simply not offered to the model and not run if
    the model asks for it anyway.

    Two switches for one door, on purpose. Hiding the declaration is what
    actually stops a tool being used, since a model cannot call what it cannot
    see -- but a conversation already in flight can carry a call to a tool that
    was visible a moment ago, and refusing at execution is what makes the gate a
    guarantee rather than a strong hint.

    Why any of this exists: removing a tool from the code is a development job
    and cannot be undone in a hurry, so it should not be the first thing that
    happens. Switching it off is reversible, survives a restart, and buys the
    time to find out whether it was really unwanted. See REVISIONE.md, entry 24.
    """

    async def disabled(self) -> frozenset[str]:
        """Return the names currently switched off, empty when none are."""
        ...


@dataclass(slots=True)
class TurnStats:
    """What the router has seen since the process started.

    Kept because three faults in one evening were noticed by the user before
    they were noticed by the service: a send that never landed, a tool that was
    never called, and a quota that had run out. Each was in the log, and nobody
    was reading the log. A running count is the cheapest thing that turns "it
    feels slow lately" into a number somebody can look at.

    Attributes:
        turns: Turns completed, degraded ones included.
        degraded: Turns that ended in a fallback rather than a real answer.
        last_reason: Why the most recent degraded turn degraded.
        last_at: When that was, as a Unix timestamp.
    """

    turns: int = 0
    degraded: int = 0
    last_reason: str | None = None
    last_at: float | None = None

    def record(self, reason: str | None) -> None:
        """Count one finished turn, and remember why if it went badly."""
        self.turns += 1
        if reason is None:
            return
        self.degraded += 1
        self.last_reason = reason
        self.last_at = time.time()

    def summary(self) -> dict[str, object]:
        """Report the tally in the shape the health endpoint publishes."""
        return {
            "turns": self.turns,
            "degraded_turns": self.degraded,
            "last_degraded_reason": self.last_reason,
            "seconds_since_degraded": (
                None if self.last_at is None else round(time.time() - self.last_at, 1)
            ),
        }


@dataclass(slots=True)
class Router:
    """Turns an :class:`AssistantRequest` into an :class:`AssistantResponse`.

    Attributes:
        llm: The language model to consult.
        memory: Where conversation history is read from and written to.
        system_prompt: The assistant personality.
        tools: Tools the model may call.  Empty in version 1.
        tool_gate: Says which of them are switched off right now, if anything
            does.  ``None`` means every registered tool is offered.
        context_providers: Facts refreshed and put in front of the model on
            every turn, for state a tool would only report when asked.
        max_tool_iterations: Ceiling on the tool rounds of a single turn.
    """

    llm: LanguageModel
    memory: ConversationMemory
    system_prompt: str
    tools: Sequence[Tool] = ()
    context_providers: Sequence[ContextProvider] = ()
    tool_gate: ToolGate | None = None
    max_tool_iterations: int = DEFAULT_MAX_TOOL_ITERATIONS
    _tools_by_name: dict[str, Tool] = field(init=False, repr=False, default_factory=dict)
    stats: TurnStats = field(init=False, repr=False, default_factory=TurnStats)

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

    def _degrade(self, reason: str, text: str, detail: str = "") -> AssistantResponse:
        """Give up on a turn in the one way the whole router gives up.

        The four ways a turn could degrade had four log formats between them
        and two severities, so "how often does this happen, and which one is
        it?" could not be answered without reading every line by hand. They now
        share a shape and a tally.

        Args:
            reason: A short stable slug, meant to be grepped and counted.
            text: What the user is told.
            detail: Anything worth adding for whoever reads the log.

        Returns:
            The degraded response, ready to return.
        """
        logger.error("turn degraded (%s)%s", reason, f": {detail}" if detail else "")
        return AssistantResponse(text=text, degraded=True, reason=reason)

    async def handle(self, request: AssistantRequest) -> AssistantResponse:
        """Run one full turn.

        This coroutine does not raise. A failure of the model, of a tool or of
        the conversation store is turned into the best answer still available,
        so the process stays alive and the user is never left without a reply.

        The three are not equally serious, and are not treated alike. The model
        being unreachable leaves nothing to say, so it produces an apology. A
        tool or a context provider failing costs one piece of information, so
        the turn continues without it. The store failing costs memory, not
        speech: the reply still goes out.

        Args:
            request: The incoming message.

        Returns:
            The reply to send back.
        """
        # One turn at a time, per channel. This method reads the history,
        # spends seconds in the model, then writes back, and nothing holds a
        # lock across that gap -- so two turns running at once on the same
        # conversation would interleave their writes. It is safe today only
        # because the Telegram adapter leaves PTB at max_concurrent_updates=1.
        # A second channel, or that setting changed, needs a per-conversation
        # lock here first. See REVISIONE.md, entry 21.
        #
        # Losing the history costs context; refusing to answer costs the turn.
        # The store is a file on a disk that can fill up and a database that a
        # long backup can hold locked, and neither is a reason to leave someone
        # staring at a silent chat.
        try:
            history = await self.memory.get_history(request.conversation_id)
        except Exception:
            logger.exception(
                "conversation=%s: could not read the history, answering without it",
                request.conversation_id,
            )
            history = []

        messages: list[Message] = [{"role": item.role, "content": item.content} for item in history]
        messages.append({"role": "user", "content": request.text})

        try:
            answer = await self._run_agentic_loop(messages)
        except LLMQuotaExceededError as exc:
            # Before LLMUnavailableError, which it inherits from.
            answer = self._degrade("quota_exhausted", _quota_message(exc.retry_after), str(exc))
        except LLMUnavailableError as exc:
            answer = self._degrade("model_unreachable", FALLBACK_UNAVAILABLE, str(exc))

        if answer.degraded:
            # A degraded turn is not worth remembering: storing it would poison
            # the window with an apology the model would then try to explain.
            self.stats.record(answer.reason)
            return answer

        self.stats.record(None)

        # The answer already exists and has already been paid for -- in tokens,
        # and against a daily quota that ran out once.  Failing to file it is a
        # reason to log loudly, never a reason to throw it away.
        try:
            await self.memory.append(
                request.conversation_id,
                StoredMessage(role="user", content=request.text),
            )
            await self.memory.append(
                request.conversation_id,
                StoredMessage(role="assistant", content=answer.text),
            )
        except Exception:
            logger.exception(
                "conversation=%s: the answer was delivered but not remembered",
                request.conversation_id,
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
        # Both gathered once per turn, not once per tool round: neither can
        # change under the assistant mid-turn, and re-reading them every round
        # would pay for them several times over for the same answer.
        switched_off = await self._switched_off()
        tool_schemas = self._tool_schemas(switched_off)
        system = await self._system_prompt_now()

        for iteration in range(1, self.max_tool_iterations + 1):
            reply = await self.llm.complete(
                system=system,
                messages=messages,
                tools=tool_schemas,
            )

            tool_uses = reply.tool_uses
            if reply.stop_reason != "tool_use" or not tool_uses:
                text = reply.text
                if not text:
                    return self._degrade(
                        "empty_answer",
                        FALLBACK_EMPTY,
                        f"stop_reason={reply.stop_reason}",
                    )
                return AssistantResponse(text=text)

            # Replay the assistant turn verbatim, then answer every tool_use
            # with a tool_result carrying the same id, in one user message.
            messages.append(reply.to_assistant_message())
            results: list[dict[str, Any]] = []
            for call in tool_uses:
                logger.info("tool round %d: running %s(%s)", iteration, call.name, call.input)
                results.append(
                    await self._execute_tool(call.id, call.name, call.input, switched_off)
                )
            messages.append({"role": "user", "content": results})

        return self._degrade(
            "tool_loop_ceiling",
            FALLBACK_TOO_MANY_STEPS,
            f"stopped after {self.max_tool_iterations} rounds",
        )

    async def _execute_tool(
        self,
        call_id: str,
        name: str,
        arguments: dict[str, Any],
        switched_off: frozenset[str] = frozenset(),
    ) -> dict[str, Any]:
        """Run one tool and wrap its outcome in a ``tool_result`` block.

        A tool that raises is reported back to the model as an error rather
        than propagated: the model is usually able to recover, and a buggy tool
        must never take the assistant down.

        Args:
            call_id: The ``tool_use`` id this result answers.
            name: Name of the requested tool.
            arguments: Arguments produced by the model.
            switched_off: Names the gate reported as off for this turn.

        Returns:
            A ``tool_result`` content block.
        """
        if name in switched_off:
            # Reachable when a call was already in flight from a turn where the
            # tool was still offered. Answered rather than ignored, so the model
            # can say why instead of falling silent.
            logger.info("refused '%s': switched off", name)
            return _tool_result(
                call_id,
                f"lo strumento {name} e' disattivato: non posso usarlo finche' "
                f"non viene riattivato",
                is_error=True,
            )

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

    async def _system_prompt_now(self) -> str:
        """Return the personality with the current facts appended to it.

        A provider that fails must not take the turn down with it: the
        assistant is more useful answering without one line of context than
        not answering at all.  The failure is logged, because a provider that
        is quietly never contributing looks exactly like one that has nothing
        to say.
        """
        if not self.context_providers:
            return self.system_prompt

        lines: list[str] = []
        for provider in self.context_providers:
            try:
                snapshot = await provider.snapshot()
            except Exception:  # a broken provider must never cost a reply
                logger.exception("context provider %r failed", type(provider).__name__)
                continue
            if snapshot:
                lines.append(snapshot.strip())

        if not lines:
            return self.system_prompt
        return self.system_prompt + "\n\n" + "\n".join(lines)

    async def _switched_off(self) -> frozenset[str]:
        """Ask the gate what is off, and treat its failure as "nothing is".

        A gate that cannot answer must not cost the turn, and of the two ways
        to be wrong this is the safer one: offering a tool that should have been
        quiet costs the user a capability they had asked to put aside, while
        hiding every tool would leave the assistant unable to do anything at all
        and unable to explain why.
        """
        if self.tool_gate is None:
            return frozenset()
        try:
            return await self.tool_gate.disabled()
        except Exception:  # a broken gate must not cost the answer
            logger.exception("could not read which tools are switched off; offering all")
            return frozenset()

    def _tool_schemas(self, switched_off: frozenset[str]) -> list[dict[str, Any]] | None:
        """Return the declarations for the API, minus anything switched off.

        ``None`` rather than an empty list when nothing is left: the clients
        take that to mean "no tools", and an empty list is not the same thing
        to every provider.
        """
        offered = [tool for tool in self.tools if tool.name not in switched_off]
        if not offered:
            return None
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.input_schema,
            }
            for tool in offered
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
