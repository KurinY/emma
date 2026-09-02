"""Tools with which EMMA can answer questions about herself.

Two: which version of her own code is running, and what she is able to do.
Both answer questions that look trivial and are not, because the alternative
to asking is the model answering from what it assumes about itself.

The version one distinguishes two situations that look identical from a chat --
the assistant behaving oddly because of a bug, and behaving oddly because the
server never received the fix.

The inventory exists because the user noticed something real: asked how many
tools she has and which, EMMA could not say. Tool declarations reach the model
through the API's own field, as functions available to call rather than as data
to read, so enumerating them is not something it can reliably do. This makes
the list a thing she can look up, like anything else she must not guess at.

The facts come from :mod:`core.version`, which prefers what the deploy wrote
down over anything it might infer.  Nothing here guesses: an assistant that
invents a version number is worse than one that says it cannot tell, since a
confident wrong answer is the one nobody thinks to check.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, ClassVar

from core import version

if TYPE_CHECKING:  # pragma: no cover - typing only
    from core.router import ToolGate

logger = logging.getLogger(__name__)


class RunningVersion:
    """Report the version and commit of the code currently running."""

    name = "running_version"
    description = (
        "Report which version of EMMA's code is actually running, with the "
        "commit and the date it was installed. Use it when the user asks what "
        "version you are, which code you are running, or whether the server is "
        "up to date with the repository. Report the commit exactly as given: it "
        "is the only field that actually answers the question. Do not answer "
        "from memory -- the version changes underneath you at every deploy."
    )
    input_schema: ClassVar[dict[str, Any]] = {"type": "object", "properties": {}}

    async def run(self, arguments: dict[str, Any]) -> str:
        """Return the running version as a sentence."""
        return version.describe()


class ToolInventory:
    """Report which tools EMMA has, and what each is for.

    Given the complete set after it is built, itself included, because there is
    no other honest way: the tools exist before the router that holds them, and
    a list assembled by hand here would be a second place to update and the
    first to be forgotten. See :meth:`describes`.
    """

    name = "list_tools"
    description = (
        "List the tools you can call, with how many there are. Use it whenever "
        "the user asks what you can do, how many tools you have, which ones, or "
        "whether you are able to do some particular thing. Pass detailed=true "
        "when they want to know what each one is for. Do not answer from "
        "memory: the set changes as EMMA is developed, and what you assume "
        "about yourself is not evidence. Tools marked disattivato have been "
        "switched off and cannot be called until enable_tool puts them back. "
        "The descriptions come back in English because they are written for "
        "you -- render them for the user in Italian, briefly."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "detailed": {
                "type": "boolean",
                "description": (
                    "True to include what each tool is for. False, the default, "
                    "returns the count and the names only."
                ),
            }
        },
    }

    def __init__(self, gate: ToolGate | None = None) -> None:
        """Build the tool with nothing to describe yet.

        Args:
            gate: Optional :class:`core.router.ToolGate`, so the listing can
                show what is switched off. Without it every tool is reported
                as available, which is true when nothing can switch them off.
        """
        self._tools: tuple[Any, ...] = ()
        self._gate = gate

    async def _switched_off(self) -> frozenset[str]:
        """Which tools are off, or none when that cannot be established.

        A gate that will not answer must not cost the listing: an inventory
        that says nothing is worse than one that omits a detail.
        """
        if self._gate is None:
            return frozenset()
        try:
            return await self._gate.disabled()
        except Exception:  # the listing is worth more than the annotation
            logger.warning("could not read which tools are switched off", exc_info=True)
            return frozenset()

    def describes(self, tools: Sequence[Any]) -> None:
        """Hand it the complete set, including itself.

        Two-phase on purpose. The alternative -- reaching into the router for
        its registry -- would make a tool depend on the thing that runs it, and
        the ``Tool`` protocol exists precisely so that nothing has to.

        Args:
            tools: Every tool the router was given.
        """
        self._tools = tuple(tools)

    async def run(self, arguments: dict[str, Any]) -> str:
        """List the tools, marking the switched-off ones and describing on request."""
        if not self._tools:  # pragma: no cover - a wiring mistake, not a state
            return "Non lo so: l'elenco degli strumenti non mi e' stato passato."

        registered = {tool.name for tool in self._tools}
        # Intersected, because a row outlives the tool it names: after a
        # removal job is carried out the name is still switched off but no
        # longer registered, and counting it would print "di cui 1
        # disattivati" with nothing in the list marked.
        off = await self._switched_off() & registered
        detailed = bool(arguments.get("detailed", False))
        count = len(self._tools)
        opening = f"Ho {count} strumenti" if count != 1 else "Ho 1 strumento"
        if off:
            # Named here because a tool that is merely absent from the listing
            # is indistinguishable from one that was never built, and the user
            # would have nothing to ask to switch back on.
            opening += f", di cui {len(off)} disattivati"

        def label(tool: Any) -> str:
            return f"{tool.name} (disattivato)" if tool.name in off else tool.name

        if not detailed:
            return f"{opening}: " + ", ".join(label(t) for t in self._tools) + "."

        lines = [f"{opening}:"]
        lines += [f"- {label(t)}: {t.description}" for t in self._tools]
        return "\n".join(lines)


def introspection_tools() -> tuple[RunningVersion]:
    """Build the tools EMMA uses to answer questions about herself.

    Returns:
        The tools, ready to hand to the router.
    """
    return (RunningVersion(),)


__all__ = ["RunningVersion", "ToolInventory", "introspection_tools"]
