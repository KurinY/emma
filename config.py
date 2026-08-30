"""Application configuration.

Every runtime setting of EMMA comes from environment variables, which are
normally supplied through a ``.env`` file sitting next to this module (see
``.env.example``).  Nothing is read from the command line and nothing is
hard-coded to a particular machine, so the very same code runs unchanged on a
developer laptop and on the production server.

The module exposes two things:

* :class:`Config` -- an immutable snapshot of the validated settings;
* :func:`load_config` -- the only supported way to build that snapshot.

Validation happens once, at start-up, and fails loudly: a missing or malformed
value raises :class:`ConfigError` with a message that names the offending
variable.  A process that survives start-up is therefore guaranteed to hold a
fully valid configuration, and no other module needs to re-check it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# --------------------------------------------------------------------------- #
# Defaults
# --------------------------------------------------------------------------- #

#: Supported LLM providers.
SUPPORTED_PROVIDERS = ("anthropic", "groq")

#: Default provider when ``LLM_PROVIDER`` is not set.
DEFAULT_LLM_PROVIDER = "anthropic"

#: Default model used when ``ANTHROPIC_MODEL`` is not set.  Sonnet 4.6 offers
#: the best balance of quality and cost for a personal conversational assistant:
#: noticeably more capable than Haiku, fast enough for chat, and cheap enough
#: for light personal traffic.
DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-4-6"

#: Default Groq model.  Llama 3.3 70B is the best openly-available model on
#: Groq's free tier for conversational quality.
DEFAULT_GROQ_MODEL = "llama-3.3-70b-versatile"

#: Default number of *messages* (not exchanges) kept in the rolling context
#: window.  Twenty messages is roughly ten user/assistant turns.
DEFAULT_MAX_HISTORY_MESSAGES = 20

#: Default location of the personality file, relative to the project root.
DEFAULT_SYSTEM_PROMPT_PATH = "prompts/system_prompt.txt"

#: Default path of the SQLite database, relative to the project root.
DEFAULT_MEMORY_DB_PATH = "data/emma.db"

#: Default backup destination on the server.  Chapter 1 of ``docs/GUIDA.pdf``
#: explains how to mount a second physical disk there.
DEFAULT_BACKUP_DIR = "/mnt/backup/emma"

#: Default number of dated archives kept by ``scripts/backup.sh``.
DEFAULT_BACKUP_KEEP = 14

#: Directory containing this file; used to resolve relative paths so that the
#: process behaves identically no matter which working directory it inherits.
PROJECT_ROOT = Path(__file__).resolve().parent


class ConfigError(RuntimeError):
    """Raised when the environment does not describe a usable configuration."""


@dataclass(frozen=True, slots=True)
class Config:
    """Validated, immutable application settings.

    Attributes:
        llm_provider: Which LLM backend to use: ``"anthropic"`` or ``"groq"``.
        anthropic_api_key: Secret key for the Anthropic API (required when
            ``llm_provider == "anthropic"``; empty otherwise).
        anthropic_model: Anthropic model identifier.
        groq_api_key: Secret key for the Groq API (required when
            ``llm_provider == "groq"``; empty otherwise).
        groq_model: Groq model identifier.
        telegram_bot_token: Token issued by BotFather for this bot.
        telegram_allowed_user_id: The single Telegram user ID allowed to talk
            to the bot.  Every other sender is ignored without a reply.
        max_history_messages: Size of the rolling conversation window.
        memory_db_path: Absolute path of the SQLite database file.
        system_prompt_path: Absolute path of the personality file.
        backup_dir: Absolute path where ``scripts/backup.sh`` writes archives.
        backup_keep: How many archives the rotation keeps.
    """

    llm_provider: str
    anthropic_api_key: str
    anthropic_model: str
    groq_api_key: str
    groq_model: str
    telegram_bot_token: str
    telegram_allowed_user_id: int
    max_history_messages: int
    memory_db_path: Path
    system_prompt_path: Path
    backup_dir: Path
    backup_keep: int

    def read_system_prompt(self) -> str:
        """Return the assistant personality as text.

        Returns:
            The content of :attr:`system_prompt_path`, stripped of trailing
            whitespace.

        Raises:
            ConfigError: If the file is missing or cannot be decoded as UTF-8.
        """
        try:
            return self.system_prompt_path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise ConfigError(
                f"cannot read the system prompt at '{self.system_prompt_path}' "
                f"(SYSTEM_PROMPT_PATH): {exc}"
            ) from exc
        except UnicodeDecodeError as exc:
            raise ConfigError(
                f"the system prompt at '{self.system_prompt_path}' "
                f"(SYSTEM_PROMPT_PATH) is not valid UTF-8: {exc}"
            ) from exc


def _require(name: str) -> str:
    """Return a mandatory environment variable.

    Args:
        name: Variable to read.

    Returns:
        The variable value with surrounding whitespace removed.

    Raises:
        ConfigError: If the variable is unset or empty.
    """
    value = os.environ.get(name, "").strip()
    if not value:
        raise ConfigError(
            f"required environment variable {name} is missing or empty; "
            f"copy .env.example to .env and fill it in"
        )
    return value


def _optional(name: str, default: str) -> str:
    """Return an optional environment variable, falling back to ``default``."""
    value = os.environ.get(name, "").strip()
    return value or default


def _positive_int(name: str, raw: str) -> int:
    """Parse ``raw`` as a strictly positive integer.

    Args:
        name: Variable name, used only to build error messages.
        raw: Text to parse.

    Returns:
        The parsed value.

    Raises:
        ConfigError: If ``raw`` is not an integer greater than zero.
    """
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer, got '{raw}'") from exc
    if value <= 0:
        raise ConfigError(f"{name} must be greater than zero, got {value}")
    return value


def _resolve(raw: str) -> Path:
    """Expand ``~`` in ``raw`` and anchor relative paths to the project root."""
    path = Path(raw).expanduser()
    return path if path.is_absolute() else (PROJECT_ROOT / path).resolve()


def load_config(env_file: Path | None = None) -> Config:
    """Load, validate and freeze the application configuration.

    Values already present in the real environment win over the ones defined in
    the ``.env`` file: this is what lets systemd, a container or a test harness
    override a single setting without editing any file.

    Args:
        env_file: Path of the dotenv file to read.  Defaults to ``.env`` next
            to this module.  A missing file is not an error -- the environment
            alone may well be complete.

    Returns:
        A fully validated :class:`Config`.

    Raises:
        ConfigError: If a mandatory variable is missing, if a numeric variable
            is malformed, or if the personality file cannot be read.
    """
    load_dotenv(env_file or (PROJECT_ROOT / ".env"), override=False)

    llm_provider = _optional("LLM_PROVIDER", DEFAULT_LLM_PROVIDER).lower()
    if llm_provider not in SUPPORTED_PROVIDERS:
        raise ConfigError(
            f"LLM_PROVIDER must be one of {SUPPORTED_PROVIDERS}, got '{llm_provider}'"
        )
    if llm_provider == "anthropic":
        anthropic_api_key = _require("ANTHROPIC_API_KEY")
        groq_api_key = _optional("GROQ_API_KEY", "")
    else:  # groq
        groq_api_key = _require("GROQ_API_KEY")
        anthropic_api_key = _optional("ANTHROPIC_API_KEY", "")

    config = Config(
        llm_provider=llm_provider,
        anthropic_api_key=anthropic_api_key,
        anthropic_model=_optional("ANTHROPIC_MODEL", DEFAULT_ANTHROPIC_MODEL),
        groq_api_key=groq_api_key,
        groq_model=_optional("GROQ_MODEL", DEFAULT_GROQ_MODEL),
        telegram_bot_token=_require("TELEGRAM_BOT_TOKEN"),
        telegram_allowed_user_id=_positive_int(
            "TELEGRAM_ALLOWED_USER_ID", _require("TELEGRAM_ALLOWED_USER_ID")
        ),
        max_history_messages=_positive_int(
            "MAX_HISTORY_MESSAGES",
            _optional("MAX_HISTORY_MESSAGES", str(DEFAULT_MAX_HISTORY_MESSAGES)),
        ),
        memory_db_path=_resolve(_optional("MEMORY_DB_PATH", DEFAULT_MEMORY_DB_PATH)),
        system_prompt_path=_resolve(_optional("SYSTEM_PROMPT_PATH", DEFAULT_SYSTEM_PROMPT_PATH)),
        backup_dir=_resolve(_optional("BACKUP_DIR", DEFAULT_BACKUP_DIR)),
        backup_keep=_positive_int(
            "BACKUP_KEEP", _optional("BACKUP_KEEP", str(DEFAULT_BACKUP_KEEP))
        ),
    )

    # Fail at start-up rather than on the first message: an unreadable
    # personality file is a configuration error, not a runtime surprise.
    config.read_system_prompt()
    return config
