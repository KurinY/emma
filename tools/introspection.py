"""Tools with which EMMA can answer questions about herself.

One so far: which version of her own code is running.  It is a small thing to
ask and a surprisingly useful one, because the honest answer distinguishes two
situations that look identical from a chat -- the assistant behaving oddly
because of a bug, and behaving oddly because the server never received the fix.

The facts come from :mod:`core.version`, which prefers what the deploy wrote
down over anything it might infer.  Nothing here guesses: an assistant that
invents a version number is worse than one that says it cannot tell, since a
confident wrong answer is the one nobody thinks to check.
"""

from __future__ import annotations

from typing import Any, ClassVar

from core import version


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


def introspection_tools() -> tuple[RunningVersion]:
    """Build the tools EMMA uses to answer questions about herself.

    Returns:
        The tools, ready to hand to the router.
    """
    return (RunningVersion(),)


__all__ = ["RunningVersion", "introspection_tools"]
