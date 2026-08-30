# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
Until 1.0.0 the public surface — the `.env` variables, the `ConversationMemory`
interface and the `Tool` protocol — may still change in a minor release; such a
change will always be listed here.

## [Unreleased]

### Added

- **Multi-provider LLM support**: select the AI backend at start-up via the
  `LLM_PROVIDER` environment variable (`"anthropic"` or `"groq"`).
- `core/llm.py`: new `GroqLanguageModel` class (OpenAI-compatible API) with
  the same exponential-backoff retry policy as `AnthropicLanguageModel`.
- `config.py`: new fields `llm_provider`, `groq_api_key`, `groq_model`;
  new constants `SUPPORTED_PROVIDERS`, `DEFAULT_LLM_PROVIDER`, `DEFAULT_GROQ_MODEL`.
- `main.py`: provider selection at boot; `/health` endpoint now exposes
  `"provider"` alongside `"model"`.
- `.env.example`: documented `LLM_PROVIDER`, `GROQ_API_KEY`, `GROQ_MODEL`.

### Changed

- Default model upgraded from `claude-haiku-4-5-20251001` to `claude-sonnet-4-6`
  for better conversational quality at acceptable cost for personal use.
- `core/llm.py`: only transient errors (`APIConnectionError`, 5xx `APIStatusError`)
  are retried; permanent 4xx errors (wrong key, bad request) now fail immediately
  instead of burning 3 seconds on pointless retries.
- `adapters/telegram.py`: blank lines (`\n\n`) near a chunk-split boundary are
  now preserved in the next chunk instead of being silently dropped.
- `tests/test_llm.py`: six tests covering the retry/no-retry distinction for the
  Anthropic client.
- `tests/test_telegram.py`: six tests for `_split_message`, including blank-line
  preservation.

The next planned step is v0.2: persistent memory on SQLite behind
the existing `ConversationMemory` interface.

## [0.1.0] - 2026-08-29

First working release: one complete loop from a Telegram message to a model
answer and back.

### Added

- **Telegram channel** (`adapters/telegram.py`) over long polling, so the
  server exposes no inbound port. Single-user whitelist through
  `TELEGRAM_ALLOWED_USER_ID`; anybody else is ignored without a reply.
- **Agentic router** (`core/router.py`) built on the Anthropic tool-use
  protocol: it calls the model, runs the tools the model asks for, feeds the
  results back and repeats until a final answer, with a ceiling on the number
  of rounds. No tool is registered in this release; registering one requires no
  change to the router.
- **Anthropic client** (`core/llm.py`) with three attempts and exponential
  backoff, a request timeout, and a dedicated error the router turns into a
  polite message instead of a crash.
- **Conversation memory** (`core/memory.py`): an abstract interface plus an
  in-memory implementation with a sliding window of `MAX_HISTORY_MESSAGES`
  messages, isolated per conversation.
- **Configuration** (`config.py`) read and validated from `.env` at start-up,
  with clear errors naming the offending variable.
- **Single-process runtime** (`main.py`): FastAPI/uvicorn owns the event loop
  and exposes `/health` on loopback; the Telegram adapter starts and stops with
  the application lifespan.
- **Structured logging** to stdout, captured by journald under systemd.
- **Deployment units** (`systemd/`): `emma.service` with `Restart=always` and
  a hardened sandbox, plus `emma-backup.service` and `.timer` for the daily
  backup.
- **Backups**: `scripts/backup.sh` writes dated, verified, permission-locked
  archives to `BACKUP_DIR` with rotation over `BACKUP_KEEP`;
  `scripts/backup-dev.ps1` does the same as zip snapshots on the Windows
  development machine.
- **Documentation**: `docs/GUIDA.md`/`.pdf`, a six-chapter Italian manual from
  a bare Ubuntu Server to a running, maintained assistant; an English README as
  the quick start; `CONTRIBUTING.md`; `CLAUDE.md` with the standing rules for
  AI assistants working on the repository; `REVISIONE.md` with a critical
  review of every design decision.
- **Quality gates**: `ruff` as formatter and linter, type hints on public
  functions, and a `pytest` suite covering the router's control flow and the
  memory window, running entirely offline.

### Known limitations

- Conversation history lives in RAM and is lost on restart.
- One channel (Telegram) and one user.
- No tools, no voice, no persistence — see the roadmap in the README.

[Unreleased]: https://github.com/KurinY/emma/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/KurinY/emma/releases/tag/v0.1.0
