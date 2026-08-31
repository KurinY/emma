"""Tests for noticing that the version stamp has stopped being true.

`/health` publishes the commit from the stamp, and an assistant asked which
version she is runs reports it with complete confidence. That confidence is
only earned if the stamp cannot quietly stop being true -- and it can. Copying
one file onto the server and restarting leaves a stamp describing a commit that
is no longer what runs. That happened twice on 31 August 2026, an hour after
the stamp was introduced to make the question answerable.

None of this prevents it. It makes the stamp admit it.
"""

from __future__ import annotations

import datetime as dt

import pytest

from core.version import STAMP_FILENAME, modified_since_deploy


def deployed(root, when: dt.datetime, files: dict[str, str] | None = None):
    """Lay out an installation stamped at `when`, with `files` written before it."""
    for name, body in (files or {}).items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
        stamp_time = when.timestamp() - 60
        import os

        os.utime(path, (stamp_time, stamp_time))

    (root / STAMP_FILENAME).write_text(
        f"version=0.3.0\ncommit=abc1234\nbuilt={when.isoformat()}\n", encoding="utf-8"
    )
    return root


def touch_after(path, when: dt.datetime, body: str = "changed"):
    """Write a file as if edited after the deploy."""
    import os

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    later = when.timestamp() + 600
    os.utime(path, (later, later))


@pytest.fixture
def when():
    return dt.datetime(2026, 9, 1, 0, 17, 22)


def test_an_untouched_deploy_reports_nothing(tmp_path, when):
    deployed(tmp_path, when, {"main.py": "print(1)", "core/llm.py": "x = 1"})

    assert modified_since_deploy(tmp_path) == []


def test_a_file_edited_after_the_deploy_is_named(tmp_path, when):
    """The exact mistake: scp one file onto the server and restart."""
    deployed(tmp_path, when, {"main.py": "print(1)"})
    touch_after(tmp_path / "prompts" / "system_prompt.txt", when)

    assert modified_since_deploy(tmp_path) == ["prompts/system_prompt.txt"]


def test_several_edits_are_all_reported_and_sorted(tmp_path, when):
    deployed(tmp_path, when, {"main.py": "print(1)"})
    touch_after(tmp_path / "z.py", when)
    touch_after(tmp_path / "a.py", when)

    assert modified_since_deploy(tmp_path) == ["a.py", "z.py"]


def test_the_live_database_is_not_evidence(tmp_path, when):
    """data/ changes every time anyone speaks to her; that is not a drift."""
    deployed(tmp_path, when, {"main.py": "print(1)"})
    touch_after(tmp_path / "data" / "emma.db", when)

    assert modified_since_deploy(tmp_path) == []


@pytest.mark.parametrize("directory", [".venv", "__pycache__", ".cache", ".git"])
def test_what_changes_on_its_own_is_ignored(tmp_path, when, directory):
    deployed(tmp_path, when, {"main.py": "print(1)"})
    touch_after(tmp_path / directory / "something", when)

    assert modified_since_deploy(tmp_path) == []


def test_the_stamp_itself_is_not_evidence(tmp_path, when):
    """It is written last, so it is always newer than what it describes."""
    deployed(tmp_path, when, {"main.py": "print(1)"})

    assert STAMP_FILENAME not in modified_since_deploy(tmp_path)


def test_no_stamp_means_the_question_does_not_arise(tmp_path):
    """On a development machine there is nothing to have drifted from."""
    (tmp_path / "main.py").write_text("print(1)", encoding="utf-8")

    assert modified_since_deploy(tmp_path) == []


def test_an_unreadable_build_time_is_survived(tmp_path, caplog):
    (tmp_path / STAMP_FILENAME).write_text("built=not-a-date\n", encoding="utf-8")

    with caplog.at_level("WARNING"):
        assert modified_since_deploy(tmp_path) == []

    assert any("unreadable build time" in r.message for r in caplog.records)


def test_a_directory_is_not_a_modified_file(tmp_path, when):
    deployed(tmp_path, when, {"main.py": "print(1)"})
    (tmp_path / "newdir").mkdir()

    assert modified_since_deploy(tmp_path) == []


def test_the_deploys_own_test_run_is_not_drift(tmp_path, when):
    """What the first real run of this check got wrong.

    The deploy script runs the suite on the server after writing the stamp, so
    pytest's cache is always newer than the build time it is compared against.
    Every deploy reported drift, and a check that cries wolf every time is
    worse than none: it teaches you to ignore the one time it is right.
    """
    deployed(tmp_path, when, {"main.py": "print(1)"})
    touch_after(tmp_path / ".pytest_cache" / "v" / "cache" / "nodeids", when)
    touch_after(tmp_path / ".ruff_cache" / "0.13.1" / "something", when)

    assert modified_since_deploy(tmp_path) == []


def test_a_real_edit_is_still_caught_alongside_the_caches(tmp_path, when):
    """Widening the exceptions must not have blunted the check."""
    deployed(tmp_path, when, {"main.py": "print(1)"})
    touch_after(tmp_path / ".pytest_cache" / "v" / "cache" / "nodeids", when)
    touch_after(tmp_path / "core" / "llm.py", when)

    assert modified_since_deploy(tmp_path) == ["core/llm.py"]
