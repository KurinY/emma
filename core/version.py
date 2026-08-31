"""What version of herself EMMA is running.

The question this answers is not "which release is this" but the more useful
one: **is the machine running what the repository says it should?**  A version
number cannot tell you that -- it changes when somebody remembers to change it
-- so what matters here is the commit.

The server has no git checkout: code arrives as a tar archive, so nothing there
can look the commit up.  It has to be written down at the moment of deploying,
which is what ``scripts/deploy.sh`` does.  Everything in this module follows
from that: the file is the source of truth in production, git is the fallback
on a development machine, and an honest "I do not know" is what remains when
neither is available -- an assistant that invents a version is worse than one
that admits it cannot tell.
"""

from __future__ import annotations

import datetime as dt
import subprocess
from pathlib import Path

#: The declared version of the assistant.  Bumped by hand when a release is
#: cut, and paired in the stamp below with the commit, which is what actually
#: identifies the code.
VERSION = "0.3.0"

#: Written by ``scripts/deploy.sh`` into the installation directory.  Not in
#: version control: it describes one deployment, not the source.
STAMP_FILENAME = "VERSION"

#: Where the project lives, resolved from this file rather than the working
#: directory, so it holds under systemd as it does from a shell.
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _read_stamp(root: Path) -> dict[str, str]:
    """Parse the ``key=value`` lines written at deploy time.

    Args:
        root: Installation directory to look in.

    Returns:
        The parsed pairs, or an empty mapping when the file is missing or
        unreadable.  A malformed stamp must not stop the assistant from
        starting: it only costs the answer to one question.
    """
    path = root / STAMP_FILENAME
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return {}

    stamp: dict[str, str] = {}
    for line in raw.splitlines():
        key, separator, value = line.partition("=")
        if separator:
            stamp[key.strip()] = value.strip()
    return stamp


def _git_commit(root: Path) -> str:
    """Return the short commit of a checkout, or ``""`` when there is none.

    Only ever true on a development machine.  Deployments have no ``.git``,
    which is precisely why the stamp exists.
    """
    if not (root / ".git").exists():
        return ""
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def _format_when(raw: str) -> str:
    """Render an ISO timestamp the way a person reads it, or pass it through."""
    try:
        moment = dt.datetime.fromisoformat(raw)
    except ValueError:
        return raw
    return moment.strftime("%d/%m/%Y alle %H:%M")


def describe(root: Path | None = None) -> str:
    """Return one sentence naming the running version, for a person to read.

    Args:
        root: Installation directory; defaults to the project root.

    Returns:
        Italian prose, because this reaches the user through EMMA.  It says
        what it knows and no more: a guessed commit would defeat the purpose of
        asking.
    """
    root = root or PROJECT_ROOT
    stamp = _read_stamp(root)

    version = stamp.get("version") or VERSION
    commit = stamp.get("commit") or _git_commit(root)
    built = stamp.get("built", "")

    if not commit:
        return (
            f"Versione dichiarata {version}, ma non so quale commit sia in "
            f"esecuzione: manca il file {STAMP_FILENAME} e qui non c'e' un "
            f"checkout git. Non posso dirti se il server e' allineato al "
            f"repository."
        )

    line = f"Versione {version}, commit {commit}"
    if built:
        line += f", installata il {_format_when(built)}"
    if not stamp:
        line += " (letto da git: questa non e' un'installazione deployata)"
    return line + "."


def summary(root: Path | None = None) -> dict[str, str]:
    """Return the same facts as fields, for ``/health``.

    Args:
        root: Installation directory; defaults to the project root.

    Returns:
        ``version``, ``commit`` and ``built``.  Unknown values are the empty
        string rather than absent, so a caller never has to guess whether a
        missing key means "unknown" or "this build is too old to say".
    """
    root = root or PROJECT_ROOT
    stamp = _read_stamp(root)
    return {
        "version": stamp.get("version") or VERSION,
        "commit": stamp.get("commit") or _git_commit(root),
        "built": stamp.get("built", ""),
    }
