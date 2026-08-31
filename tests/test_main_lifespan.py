"""Tests for wiring the application together and taking it apart again.

`main.py` is the composition root, and until now the only module with no tests
of its own -- which is awkward, because a fault here does not degrade a reply,
it decides whether the process runs at all.

Both cases guarded below were real defects. Start-up used to open the two
databases *before* entering the try block, so a Telegram failure - a bad token,
a network that is not there yet - left their connections and write-ahead logs
behind on a process that was already on its way out. And shutdown ran the four
steps in sequence, so the first one to raise stranded the three after it.
"""

from __future__ import annotations

import dataclasses
import pathlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import main
from config import Config
from core.memory import SqliteConversationMemory
from main import create_app


def build_config(tmp_path: pathlib.Path) -> Config:
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("Sei EMMA.", encoding="utf-8")
    return Config(
        llm_provider="groq",
        anthropic_api_key="",
        anthropic_model="claude-test",
        groq_api_key="gsk_test",
        groq_model="test-model",
        telegram_bot_token="123:AA",
        telegram_allowed_user_id=1,
        max_history_messages=20,
        memory_db_path=tmp_path / "data" / "emma.db",
        system_prompt_path=prompt,
        backup_dir=tmp_path / "backup",
        backup_keep=14,
    )


@pytest.fixture
def no_telegram():
    """Keep the adapter from touching the network while the app is built."""
    with patch("main.TelegramAdapter") as adapter:
        instance = adapter.return_value
        instance.start = AsyncMock()
        instance.stop = AsyncMock()
        # Healthy by default; the tests that care set it themselves.
        instance.is_listening = True
        yield instance


async def run_lifespan(app, *, expect_failure: bool = False) -> None:
    """Enter and leave the application's lifespan once."""
    manager = app.router.lifespan_context(app)
    if expect_failure:
        with pytest.raises(RuntimeError):
            await manager.__aenter__()
        return
    await manager.__aenter__()
    await manager.__aexit__(None, None, None)


async def test_the_application_starts_and_stops_cleanly(tmp_path, no_telegram):
    app = create_app(build_config(tmp_path))

    await run_lifespan(app)

    no_telegram.start.assert_awaited_once()
    no_telegram.stop.assert_awaited_once()


async def test_the_database_is_created_on_start_up(tmp_path, no_telegram):
    config = build_config(tmp_path)
    app = create_app(config)

    await run_lifespan(app)

    assert config.memory_db_path.exists()


@pytest.fixture
def watched_close():
    """Watch ``close`` being called without preventing it from happening.

    Replacing it outright leaves the real aiosqlite connection open, and its
    background thread then keeps the test session alive after the assertion has
    passed -- a test that leaks the very resource it exists to prove is
    released. ``autospec`` keeps the descriptor, so ``self`` still arrives and
    the real method still runs.
    """
    real = SqliteConversationMemory.close
    with patch.object(
        main.SqliteConversationMemory, "close", autospec=True, side_effect=real
    ) as spy:
        yield spy


async def test_a_failed_start_up_closes_what_it_had_opened(tmp_path, no_telegram, watched_close):
    """The defect: the databases stayed open when Telegram failed to start."""
    no_telegram.start.side_effect = RuntimeError("no token")
    app = create_app(build_config(tmp_path))

    await run_lifespan(app, expect_failure=True)

    watched_close.assert_awaited_once()


async def test_a_failed_start_up_does_not_stop_what_never_started(tmp_path, no_telegram):
    no_telegram.start.side_effect = RuntimeError("no token")
    app = create_app(build_config(tmp_path))

    await run_lifespan(app, expect_failure=True)

    no_telegram.stop.assert_not_awaited()


async def test_one_failing_shutdown_step_does_not_strand_the_others(
    tmp_path, no_telegram, watched_close
):
    """The defect: the first raise skipped every step after it."""
    no_telegram.stop.side_effect = RuntimeError("stuck")
    app = create_app(build_config(tmp_path))

    await run_lifespan(app)

    watched_close.assert_awaited_once()


async def test_a_failing_shutdown_is_reported_not_swallowed(tmp_path, no_telegram, caplog):
    no_telegram.stop.side_effect = RuntimeError("stuck")
    app = create_app(build_config(tmp_path))

    with caplog.at_level("ERROR"):
        await run_lifespan(app)

    assert any("failed to shut down" in r.message for r in caplog.records)


async def test_health_reports_the_model_and_the_running_commit(tmp_path, no_telegram):
    from fastapi.testclient import TestClient

    config = build_config(tmp_path)
    app = create_app(config)

    with TestClient(app) as client:
        body = client.get("/health").json()

    assert body["status"] == "ok"
    assert body["model"] == config.groq_model
    assert body["provider"] == "groq"
    # Present even when unknown, so a caller never has to guess why it is absent.
    assert set(body) >= {"version", "commit", "built", "uptime_seconds"}


async def test_the_router_gets_the_tools_and_the_context_provider(tmp_path, no_telegram):
    """The wiring the assistant's usefulness depends on, asserted once."""
    with patch("main.Router") as router:
        create_app(build_config(tmp_path))

    kwargs = router.call_args.kwargs
    assert {t.name for t in kwargs["tools"]} == {
        "request_development",
        "work_status",
        "answer_question",
        "abandon_development",
        "running_version",
    }
    assert len(kwargs["context_providers"]) == 1


async def test_the_provider_choice_follows_the_configuration(tmp_path, no_telegram):
    config = dataclasses.replace(
        build_config(tmp_path), llm_provider="anthropic", anthropic_api_key="sk-x"
    )

    with patch("main.AnthropicLanguageModel") as anthropic_client:
        create_app(config)

    anthropic_client.assert_called_once()


# --------------------------------------------------------------------------- #
# A health endpoint that can report ill health
# --------------------------------------------------------------------------- #
#
# It used to answer "ok" unconditionally, which makes it a liveness check
# wearing the wrong name: nothing it could ever say would tell you something
# was wrong. Three faults in one evening were noticed by the user first.


def get_health(app, expect: int = 200) -> dict:
    from fastapi.testclient import TestClient

    with TestClient(app, raise_server_exceptions=False) as client:
        reply = client.get("/health")
    assert reply.status_code == expect
    return reply.json()


async def test_a_working_store_reports_ok(tmp_path, no_telegram):
    body = get_health(create_app(build_config(tmp_path)))

    assert body["status"] == "ok"
    assert body["store"] == "ok"


async def test_an_unreadable_store_is_reported_as_degraded(tmp_path, no_telegram):
    """The case the old endpoint could not express at all."""
    app = create_app(build_config(tmp_path))

    with patch.object(
        main.SqliteConversationMemory,
        "get_history",
        autospec=True,
        side_effect=OSError("database is locked"),
    ):
        body = get_health(app, expect=503)

    assert body["status"] == "degraded"
    assert "OSError" in body["store"]


async def test_the_status_code_carries_the_same_verdict_as_the_body(tmp_path, no_telegram):
    """So a checker that understands nothing but HTTP still gets it right."""
    get_health(create_app(build_config(tmp_path)), expect=200)


async def test_the_probe_does_not_invent_a_conversation(tmp_path, no_telegram):
    """It reads; it must never leave a trace in the history it is inspecting."""
    from main import HEALTH_PROBE_CONVERSATION

    config = build_config(tmp_path)
    app = create_app(config)
    get_health(app)

    memory = SqliteConversationMemory(db_path=config.memory_db_path, max_messages=20)
    await memory.open()
    try:
        assert await memory.get_history(HEALTH_PROBE_CONVERSATION) == []
    finally:
        await memory.close()


async def test_the_tally_starts_empty_and_is_published(tmp_path, no_telegram):
    body = get_health(create_app(build_config(tmp_path)))

    assert body["turns"] == 0
    assert body["degraded_turns"] == 0
    assert body["last_degraded_reason"] is None
    assert body["seconds_since_degraded"] is None


# --------------------------------------------------------------------------- #
# Start-up failures the user can fix
# --------------------------------------------------------------------------- #
#
# config.py exists so that a mistake in .env produces one sentence naming the
# variable, at start-up, rather than a crash hours later. Building the
# application had no such courtesy: switching LLM_PROVIDER without reinstalling
# gave `ModuleNotFoundError: No module named 'groq'` and a stack trace, which
# names the symptom and not the fix.


def without_groq(monkeypatch):
    """Make `import groq` fail the way an incomplete install does."""
    import builtins

    real = builtins.__import__

    def fake(name, *args, **kwargs):
        if name == "groq":
            raise ModuleNotFoundError("No module named 'groq'")
        return real(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake)


async def test_a_missing_provider_package_names_the_fix(tmp_path, monkeypatch, no_telegram):
    from core.llm import MissingDependencyError

    without_groq(monkeypatch)

    with pytest.raises(MissingDependencyError) as raised:
        create_app(build_config(tmp_path))

    message = str(raised.value)
    assert "groq" in message
    assert "requirements.txt" in message  # what to actually do about it


@pytest.fixture
def keep_the_log_handlers(monkeypatch):
    """Stop main() from reconfiguring logging out from under caplog.

    configure_logging() calls basicConfig(force=True), which drops every
    handler including the one pytest installed to record what was said. The
    message was being logged correctly all along; only the test could not see
    it.
    """
    monkeypatch.setattr(main, "configure_logging", lambda: None)


def test_the_entry_point_reports_it_without_a_traceback(
    tmp_path, monkeypatch, caplog, keep_the_log_handlers
):
    """Exit 2, one line, no stack frames -- the same as a bad .env."""
    from core.llm import MissingDependencyError

    monkeypatch.setattr(main, "load_config", lambda: build_config(tmp_path))
    monkeypatch.setattr(
        main, "create_app", lambda _c: (_ for _ in ()).throw(MissingDependencyError("install it"))
    )
    served = MagicMock()
    monkeypatch.setattr(main, "uvicorn", MagicMock(run=served))

    with caplog.at_level("ERROR"):
        code = main.main()

    assert code == 2
    assert any("install it" in r.message for r in caplog.records)
    served.assert_not_called()


def test_a_bad_configuration_is_still_reported_the_same_way(
    tmp_path, monkeypatch, caplog, keep_the_log_handlers
):
    """The branch this one was added next to must keep working."""
    from config import ConfigError

    monkeypatch.setattr(
        main,
        "load_config",
        lambda: (_ for _ in ()).throw(ConfigError("TELEGRAM_BOT_TOKEN missing")),
    )

    with caplog.at_level("ERROR"):
        code = main.main()

    assert code == 2
    assert any("TELEGRAM_BOT_TOKEN" in r.message for r in caplog.records)


def test_a_working_configuration_reaches_the_server(tmp_path, monkeypatch, no_telegram):
    """So the guard cannot pass by refusing to start at all."""
    monkeypatch.setattr(main, "load_config", lambda: build_config(tmp_path))
    served = MagicMock()
    monkeypatch.setattr(main, "uvicorn", MagicMock(run=served))

    assert main.main() == 0
    served.assert_called_once()


# --------------------------------------------------------------------------- #
# A bot that has gone deaf
# --------------------------------------------------------------------------- #
#
# The process being alive says nothing about whether Telegram updates are still
# arriving. Long polling can stop on its own -- PTB giving up after repeated
# network failures on a host that loses one connection in twenty -- while
# uvicorn, the store and the model client all carry on. The health check knew
# nothing about it and answered "ok", which is the difference between a service
# that reports its own fault and one where the user notices first. Twice on
# 31 August 2026 the user noticed first.


async def test_health_says_it_is_listening_when_it_is(tmp_path, no_telegram):
    no_telegram.is_listening = True

    body = get_health(create_app(build_config(tmp_path)))

    assert body["telegram"] == "listening"
    assert body["status"] == "ok"


async def test_a_bot_that_stopped_polling_is_degraded(tmp_path, no_telegram):
    """The blind spot: everything else healthy, nobody able to reach her."""
    no_telegram.is_listening = False

    body = get_health(create_app(build_config(tmp_path)), expect=503)

    assert body["telegram"] == "not polling"
    assert body["status"] == "degraded"


async def test_a_bot_that_stopped_polling_is_logged(tmp_path, no_telegram, caplog):
    no_telegram.is_listening = False

    with caplog.at_level("ERROR"):
        get_health(create_app(build_config(tmp_path)), expect=503)

    assert any("long polling is not running" in r.message for r in caplog.records)


async def test_a_working_store_does_not_excuse_a_deaf_bot(tmp_path, no_telegram):
    """Both have to hold; neither alone is health."""
    no_telegram.is_listening = False

    body = get_health(create_app(build_config(tmp_path)), expect=503)

    assert body["store"] == "ok"
    assert body["status"] == "degraded"
