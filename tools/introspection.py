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
        "Dice quale versione del codice di EMMA e' effettivamente in esecuzione, "
        "con il commit e la data di installazione. Usalo quando l'utente chiede "
        "che versione sei, quale codice stai eseguendo, se il server e' "
        "aggiornato o allineato al repository. Riporta il commit cosi' com'e': "
        "e' l'unico dato che risponde davvero alla domanda."
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
