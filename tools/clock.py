"""What time it is, where the user is.

Commissioned as job #9. The smallest possible tool -- no network, no key, no
new dependency -- but it has two ways to be quietly wrong, and both were found
by running it on the two machines rather than by reasoning about it.

**The timezone is not the server's.** The question is always "what time is it
for me", and the user is in Italy. Today the server happens to agree, so
reading the system clock would look correct and would stay correct until
somebody moved the machine or changed its zone -- a wrong answer with nothing
to indicate it. The zone is therefore named here, and daylight saving comes
from the zone database rather than from arithmetic on an offset: Italy is
UTC+1 in winter and UTC+2 in summer, so a fixed offset is right for half the
year.

**The names are not the locale's.** The service runs under `LC_ALL=C`, where
``strftime('%A')`` answers "Wednesday". The Italian names are spelled out below
instead, which also makes the output identical on every machine.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any, ClassVar

logger = logging.getLogger(__name__)

#: Where the user is, not where the server is. One line to change if that ever
#: stops being true, and the only thing in this module that is a choice rather
#: than a fact.
TIMEZONE = "Europe/Rome"

#: Written out because the process runs under the C locale, where strftime
#: answers in English. Index 0 is Monday, as ``weekday()`` counts.
_DAYS = (
    "lunedi'",
    "martedi'",
    "mercoledi'",
    "giovedi'",
    "venerdi'",
    "sabato",
    "domenica",
)

#: Index 1 is January, as ``month`` counts; index 0 is never used.
_MONTHS = (
    "",
    "gennaio",
    "febbraio",
    "marzo",
    "aprile",
    "maggio",
    "giugno",
    "luglio",
    "agosto",
    "settembre",
    "ottobre",
    "novembre",
    "dicembre",
)


def _now() -> tuple[dt.datetime, bool]:
    """Return the time in :data:`TIMEZONE`, and whether the zone was available.

    Windows ships no time zone database, so ``ZoneInfo`` there needs the
    ``tzdata`` package, which is not a dependency of this project. Rather than
    add one for a development machine, the fall back is the system clock -- and
    it says so, because a time that is silently an hour out is worse than one
    that admits it might be.

    Returns:
        The current moment, and ``True`` when it came from the named zone.
    """
    try:
        from zoneinfo import ZoneInfo

        return dt.datetime.now(ZoneInfo(TIMEZONE)), True
    except Exception as exc:  # no tz database on this machine
        logger.warning(
            "no time zone database for %s (%s); falling back to the system clock, "
            "which is only right if this machine is set to that zone",
            TIMEZONE,
            exc,
        )
        return dt.datetime.now(), False


def describe_now() -> str:
    """Render the current time the way a person would say it."""
    now, exact = _now()
    when = (
        f"Sono le {now.hour:02d}:{now.minute:02d} di {_DAYS[now.weekday()]} "
        f"{now.day} {_MONTHS[now.month]} {now.year}."
    )
    if not exact:
        when += " (ora di sistema: il fuso non e' verificabile su questa macchina)"
    return when


class CurrentTime:
    """Tell the user what time it is."""

    name = "current_time"
    description = (
        "Give the current date and time where the user is. Call it whenever "
        "they ask what time or what day it is, or when answering needs today's "
        "date -- how many days until something, what day of the week a date "
        "falls on. You have no clock of your own and cannot infer the time from "
        "the conversation, so guessing is always wrong."
    )
    input_schema: ClassVar[dict[str, Any]] = {"type": "object", "properties": {}}

    async def run(self, arguments: dict[str, Any]) -> str:
        """Return the time. Takes no arguments; any given are ignored."""
        return describe_now()


def clock_tools() -> tuple[CurrentTime]:
    """Build the clock tool.

    Returns:
        The tool, ready to hand to the router.
    """
    return (CurrentTime(),)


__all__ = ["TIMEZONE", "CurrentTime", "clock_tools", "describe_now"]
