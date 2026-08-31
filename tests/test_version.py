"""Tests for reporting which code is actually running.

The question worth answering is not "which release is this" but "is the server
running what the repository says". Only the commit answers that, and on a
deployment there is no git checkout to ask -- code arrives as an archive -- so
the deploy has to write it down.

What these tests mostly guard is the honest failure: when nothing knows the
commit, EMMA must say so. A confident wrong version is the answer nobody thinks
to check, which is the whole reason the user asked for this.
"""

from __future__ import annotations

import pathlib

import pytest

from core import version
from core.router import Tool
from tools.introspection import RunningVersion, introspection_tools


@pytest.fixture
def stamped(tmp_path: pathlib.Path) -> pathlib.Path:
    (tmp_path / version.STAMP_FILENAME).write_text(
        "version=9.9.9\ncommit=abc1234\nbuilt=2026-08-31T14:20:00+02:00\n",
        encoding="utf-8",
    )
    return tmp_path


# --------------------------------------------------------------------------- #
# Reading the stamp
# --------------------------------------------------------------------------- #


def test_the_stamp_is_preferred_over_anything_inferred(stamped):
    line = version.describe(stamped)

    assert "9.9.9" in line
    assert "abc1234" in line


def test_the_install_date_is_rendered_for_a_person(stamped):
    assert "31/08/2026 alle 14:20" in version.describe(stamped)


def test_a_malformed_date_is_passed_through_rather_than_dropped(tmp_path):
    (tmp_path / version.STAMP_FILENAME).write_text(
        "version=1.0\ncommit=deadbee\nbuilt=non-una-data\n", encoding="utf-8"
    )

    assert "non-una-data" in version.describe(tmp_path)


def test_blank_lines_and_junk_do_not_break_the_stamp(tmp_path):
    (tmp_path / version.STAMP_FILENAME).write_text(
        "\nversion=2.0\nrumore senza uguale\ncommit=cafe123\n\n", encoding="utf-8"
    )

    line = version.describe(tmp_path)

    assert "2.0" in line and "cafe123" in line


# --------------------------------------------------------------------------- #
# When nothing knows
# --------------------------------------------------------------------------- #


def test_without_a_stamp_or_a_checkout_it_admits_it(tmp_path):
    """The failure that matters: never invent a commit."""
    line = version.describe(tmp_path)

    assert "non so quale commit" in line
    assert version.VERSION in line


def test_the_admission_says_why_it_cannot_tell(tmp_path):
    line = version.describe(tmp_path)

    assert version.STAMP_FILENAME in line
    assert "allineato" in line


def test_an_unreadable_stamp_is_treated_as_absent(tmp_path):
    # A directory where the file should be: read fails, and must not raise.
    (tmp_path / version.STAMP_FILENAME).mkdir()

    assert "non so quale commit" in version.describe(tmp_path)


# --------------------------------------------------------------------------- #
# The fields behind /health
# --------------------------------------------------------------------------- #


def test_the_summary_carries_the_three_fields(stamped):
    assert version.summary(stamped) == {
        "version": "9.9.9",
        "commit": "abc1234",
        "built": "2026-08-31T14:20:00+02:00",
    }


def test_unknown_fields_are_empty_not_missing(tmp_path):
    """A caller must never have to guess why a key is absent."""
    summary = version.summary(tmp_path)

    assert set(summary) == {"version", "commit", "built"}
    assert summary["commit"] == ""
    assert summary["built"] == ""


def test_the_declared_version_stands_in_when_the_stamp_has_none(tmp_path):
    (tmp_path / version.STAMP_FILENAME).write_text("commit=abc\n", encoding="utf-8")

    assert version.summary(tmp_path)["version"] == version.VERSION


# --------------------------------------------------------------------------- #
# The tool
# --------------------------------------------------------------------------- #


async def test_the_tool_reports_the_running_version():
    line = await RunningVersion().run({})

    assert version.VERSION in line or "commit" in line


async def test_the_tool_satisfies_the_protocol():
    for tool in introspection_tools():
        assert isinstance(tool, Tool)


async def test_the_tool_name_is_stable():
    """It is named in the personality; renaming it silently breaks that."""
    assert RunningVersion().name == "running_version"
