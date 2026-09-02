"""Tests for the clock tool.

Commissioned as job #9. Small, and with two ways to be quietly wrong that only
showed up by running it on both machines: the time zone and the language of the
names. Both are pinned here, because a clock that is an hour out or answers
"Wednesday" fails in a way the user has no reason to doubt.
"""

from __future__ import annotations

import datetime as dt
from unittest.mock import patch

import pytest

from core.router import Tool
from tools.clock import TIMEZONE, clock_tools, describe_now


def frozen(moment: dt.datetime, exact: bool = True):
    """Pin the clock so the rendering can be asserted exactly."""
    return patch("tools.clock._now", return_value=(moment, exact))


def test_the_tool_satisfies_the_protocol():
    (tool,) = clock_tools()

    assert isinstance(tool, Tool)
    assert tool.name == "current_time"


def test_it_takes_no_arguments():
    """Nothing to get wrong, and nothing to describe: the schema says so."""
    (tool,) = clock_tools()

    assert tool.input_schema["properties"] == {}
    assert "required" not in tool.input_schema


async def test_it_answers_with_the_time():
    (tool,) = clock_tools()

    with frozen(dt.datetime(2026, 9, 2, 20, 7)):
        assert await tool.run({}) == "Sono le 20:07 di mercoledi' 2 settembre 2026."


async def test_unexpected_arguments_are_ignored_rather_than_fatal():
    """A model may pass something anyway; that must not cost the turn."""
    (tool,) = clock_tools()

    with frozen(dt.datetime(2026, 9, 2, 20, 7)):
        assert "20:07" in await tool.run({"timezone": "Mars"})


# --------------------------------------------------------------------------- #
# The two ways it could be quietly wrong
# --------------------------------------------------------------------------- #


def test_the_zone_is_the_users_not_the_servers():
    """Reading the system clock would look right until the machine moved."""
    assert TIMEZONE == "Europe/Rome"


@pytest.mark.parametrize(
    ("moment", "expected"),
    [
        (dt.datetime(2026, 9, 2), "mercoledi'"),
        (dt.datetime(2026, 9, 3), "giovedi'"),
        (dt.datetime(2026, 9, 5), "sabato"),
        (dt.datetime(2026, 9, 6), "domenica"),
        (dt.datetime(2026, 9, 7), "lunedi'"),
    ],
)
def test_the_days_are_italian_whatever_the_locale(moment, expected):
    """The service runs under LC_ALL=C, where strftime answers "Wednesday"."""
    with frozen(moment):
        assert expected in describe_now()


@pytest.mark.parametrize(
    ("month", "expected"),
    [(1, "gennaio"), (5, "maggio"), (8, "agosto"), (9, "settembre"), (12, "dicembre")],
)
def test_the_months_are_italian_too(month, expected):
    with frozen(dt.datetime(2026, month, 15)):
        assert expected in describe_now()


def test_midnight_is_not_rendered_as_hour_zero_missing_a_digit():
    with frozen(dt.datetime(2026, 1, 1, 0, 5)):
        assert describe_now() == "Sono le 00:05 di giovedi' 1 gennaio 2026."


def test_an_unverifiable_zone_is_admitted_not_hidden():
    """A time silently an hour out is worse than one that says it might be."""
    with frozen(dt.datetime(2026, 9, 2, 20, 7), exact=False):
        answer = describe_now()

    assert "20:07" in answer
    assert "non e' verificabile" in answer


def test_the_real_clock_produces_something_plausible():
    """No mock: whatever this machine has, the output must still parse."""
    answer = describe_now()

    assert answer.startswith("Sono le ")
    assert any(
        m in answer
        for m in (
            "gennaio",
            "settembre",
            "dicembre",
            "febbraio",
            "marzo",
            "aprile",
            "maggio",
            "giugno",
            "luglio",
            "agosto",
            "ottobre",
            "novembre",
        )
    )


def test_the_missing_zone_database_is_reported(caplog):
    """The fallback has to be visible in the log, not only in the answer."""
    import tools.clock

    with patch.dict("sys.modules", {"zoneinfo": None}), caplog.at_level("WARNING"):
        _, exact = tools.clock._now()

    assert not exact
    assert any("falling back to the system clock" in r.message for r in caplog.records)
