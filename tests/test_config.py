"""Tests for reading and validating the configuration.

This module decides whether the process starts at all, and it is the one place
where a mistake is the user's rather than the code's: a typo in `.env`, a
variable never filled in, a path that does not exist. What it owes them is an
error naming the offending variable, at start-up, instead of a crash on the
first message hours later.

Every case here builds its own `.env` in a temporary directory. The real one is
never read, and nothing in this file contains a credential.
"""

from __future__ import annotations

import pathlib

import pytest

from config import (
    DEFAULT_GROQ_MODEL,
    DEFAULT_MAX_HISTORY_MESSAGES,
    SUPPORTED_PROVIDERS,
    ConfigError,
    load_config,
)

MINIMAL = """
TELEGRAM_BOT_TOKEN=123456789:AAtest
TELEGRAM_ALLOWED_USER_ID=4242
GROQ_API_KEY=gsk_test
LLM_PROVIDER=groq
"""


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    """Real environment variables win over `.env`, so clear the ones we set.

    Without this a variable exported in the developer's shell would quietly
    decide the outcome of a test, and the failure would look like a bug in the
    loader rather than in the test.
    """
    for name in (
        "LLM_PROVIDER",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_MODEL",
        "GROQ_API_KEY",
        "GROQ_MODEL",
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_ALLOWED_USER_ID",
        "MAX_HISTORY_MESSAGES",
        "MEMORY_DB_PATH",
        "SYSTEM_PROMPT_PATH",
        "BACKUP_DIR",
        "BACKUP_KEEP",
    ):
        monkeypatch.delenv(name, raising=False)


def write_env(tmp_path: pathlib.Path, body: str) -> pathlib.Path:
    path = tmp_path / ".env"
    path.write_text(body, encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
# The happy path
# --------------------------------------------------------------------------- #


def test_a_minimal_env_is_enough(tmp_path):
    config = load_config(env_file=write_env(tmp_path, MINIMAL))

    assert config.llm_provider == "groq"
    assert config.telegram_allowed_user_id == 4242


def test_the_optional_values_have_defaults(tmp_path):
    config = load_config(env_file=write_env(tmp_path, MINIMAL))

    assert config.max_history_messages == DEFAULT_MAX_HISTORY_MESSAGES
    assert config.groq_model == DEFAULT_GROQ_MODEL
    assert config.backup_keep > 0


def test_a_real_environment_variable_beats_the_file(tmp_path, monkeypatch):
    """What lets systemd or a test override one setting without editing files."""
    monkeypatch.setenv("MAX_HISTORY_MESSAGES", "7")

    config = load_config(env_file=write_env(tmp_path, MINIMAL))

    assert config.max_history_messages == 7


# --------------------------------------------------------------------------- #
# Refusing to start, with the reason
# --------------------------------------------------------------------------- #


def test_a_missing_required_variable_names_itself(tmp_path):
    body = MINIMAL.replace("TELEGRAM_BOT_TOKEN=123456789:AAtest", "")

    with pytest.raises(ConfigError, match="TELEGRAM_BOT_TOKEN"):
        load_config(env_file=write_env(tmp_path, body))


def test_an_empty_required_variable_counts_as_missing(tmp_path):
    body = MINIMAL.replace("GROQ_API_KEY=gsk_test", "GROQ_API_KEY=")

    with pytest.raises(ConfigError, match="GROQ_API_KEY"):
        load_config(env_file=write_env(tmp_path, body))


def test_a_user_id_that_is_not_a_number_says_so(tmp_path):
    """The commonest mistake: pasting the @handle instead of the numeric id."""
    body = MINIMAL.replace("TELEGRAM_ALLOWED_USER_ID=4242", "TELEGRAM_ALLOWED_USER_ID=@matteo")

    with pytest.raises(ConfigError, match="TELEGRAM_ALLOWED_USER_ID"):
        load_config(env_file=write_env(tmp_path, body))


def test_an_unknown_provider_lists_the_known_ones(tmp_path):
    body = MINIMAL.replace("LLM_PROVIDER=groq", "LLM_PROVIDER=openai")

    with pytest.raises(ConfigError) as raised:
        load_config(env_file=write_env(tmp_path, body))

    for provider in SUPPORTED_PROVIDERS:
        assert provider in str(raised.value)


def test_choosing_a_provider_requires_its_key(tmp_path):
    """Switching provider without adding the key is a one-line mistake."""
    body = MINIMAL.replace("LLM_PROVIDER=groq", "LLM_PROVIDER=anthropic")

    with pytest.raises(ConfigError, match="ANTHROPIC_API_KEY"):
        load_config(env_file=write_env(tmp_path, body))


def test_the_other_provider_key_is_not_required(tmp_path):
    """Running on Groq must not demand an Anthropic key that is never used."""
    config = load_config(env_file=write_env(tmp_path, MINIMAL))

    assert config.anthropic_api_key == ""


@pytest.mark.parametrize("bad", ["zero", "0", "-3", "3.5"])
def test_a_window_that_is_not_a_positive_integer_is_refused(tmp_path, bad):
    body = MINIMAL + f"\nMAX_HISTORY_MESSAGES={bad}\n"

    with pytest.raises(ConfigError, match="MAX_HISTORY_MESSAGES"):
        load_config(env_file=write_env(tmp_path, body))


@pytest.mark.parametrize("bad", ["zero", "0", "-1"])
def test_a_bad_backup_count_is_caught_at_start_up(tmp_path, bad):
    """BACKUP_KEEP is only read by a script at 03:30, so it is validated here.

    Discovering it at three in the morning, in a job nobody is watching, is the
    outcome this test exists to prevent.
    """
    body = MINIMAL + f"\nBACKUP_KEEP={bad}\n"

    with pytest.raises(ConfigError, match="BACKUP_KEEP"):
        load_config(env_file=write_env(tmp_path, body))


def test_an_unreadable_personality_file_is_a_start_up_error(tmp_path):
    """Otherwise it would surface on the first message, hours later."""
    body = MINIMAL + f"\nSYSTEM_PROMPT_PATH={tmp_path / 'nowhere.txt'}\n"

    with pytest.raises(ConfigError, match="SYSTEM_PROMPT_PATH"):
        load_config(env_file=write_env(tmp_path, body))


# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #


def test_a_relative_path_is_anchored_to_the_project(tmp_path):
    """So that the service works the same started from /opt/emma or from /."""
    config = load_config(env_file=write_env(tmp_path, MINIMAL))

    assert config.memory_db_path.is_absolute()


def test_an_absolute_path_is_taken_as_given(tmp_path):
    """scripts/backup.sh resolves it the same way; the two must not disagree."""
    elsewhere = tmp_path / "altrove" / "emma.db"
    body = MINIMAL + f"\nMEMORY_DB_PATH={elsewhere}\n"

    config = load_config(env_file=write_env(tmp_path, body))

    assert config.memory_db_path == elsewhere
