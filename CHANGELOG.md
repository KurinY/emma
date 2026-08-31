# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
Until 1.0.0 the public surface — the `.env` variables, the `ConversationMemory`
interface and the `Tool` protocol — may still change in a minor release; such a
change will always be listed here.

## [Unreleased]

### Added

- **Context providers**: a `ContextProvider` protocol in `core/router.py` for
  state that must be in front of the model rather than fetched by it. The
  router asks each provider once per turn — not once per tool round, since the
  state cannot change mid-turn — and appends what it returns to the system
  prompt. A provider that fails is logged and skipped: answering without one
  line of context beats not answering at all.
- `DevelopmentContext` reports the shape of the queue in one line, counts and
  numbers only, and says plainly which source wins when memory disagrees.
- `tests/test_router_context.py`: 13 tests, including that a broken provider
  does not degrade the turn and that the state is read once per turn however
  many tool rounds it takes.

### Fixed

- **A failure of the conversation store took the whole turn down.** Neither
  the read at the start of a turn nor the two writes at the end were guarded,
  so a locked database — a long backup holds one — or a full disk produced no
  reply at all. From a phone that is indistinguishable from a dead bot: the
  same silence that hid the Telegram send failures, arriving by another route.

  Neither fault is a reason not to speak, and they are not equally costly.
  Losing the history costs context, so the turn now continues without it. The
  writes happen *after* the model has answered — tokens already spent, against
  a daily quota that has run out once — so failing to file that answer must
  never be allowed to discard it. Both are logged at ERROR with the reason.

- **A rate limit was treated as a permanent failure, and reported as an
  outage.** On the evening of 31 August 2026 the Groq daily token quota ran
  out. The log recorded exactly why — `on tokens per day (TPD): Limit 200000,
  Used 199048 … Please try again in 11m24.288s` — and the user was told *"Non
  riesco a contattare il cervello in questo momento, riprova tra poco"*: the
  wrong diagnosis, and advice that could only fail. They spent the evening
  guessing why the assistant had gone quiet.

  Two faults met at that point. HTTP 429 is neither the transient 5xx that is
  always worth retrying nor the permanent 4xx that never is, and it had been
  filed with the latter — so a per-minute limit, which clears in seconds, was
  never retried either. And the message written for an unreachable model was
  reused for a model that had answered and refused.

  A 429 now gets a branch of its own in both clients, placed before the general
  status-error clause it inherits from. When the server says how long to wait,
  that is weighed against what the remaining retries could actually absorb —
  three seconds by default: a short wait is retried, a long one is refused at
  once rather than made slower. `LLMQuotaExceededError` carries the wait up to
  the router, which now says that the limit was reached, that it is not a
  fault, and when to come back: *"Riprova fra circa 11 minuti."*

- **A tool is consulted only when the model decides to consult it, and it
  decided wrong.** Asked which jobs were pending, EMMA reported one of two and
  described it with the reading the user had explicitly rejected; the logs said
  `tools=0`. She had not called the tool at all — she had repeated, word for
  word, a wrong answer given fifteen minutes earlier and since kept in
  persistent memory.

  This is memory (0.2.0) and tools (0.3.0) damaging each other: an answer
  derived from a tool, once stored, is indistinguishable from a fact and gets
  reused instead of re-asked. Nothing about it is specific to development jobs
  — it applies to any tool reporting state that changes.

  Measured, ten attempts per configuration: 6/10 correct as found, 8/10 with a
  context provider, 9/10 with a clean history and no provider, 10/10 with both.
  Instructing the model instead would move those numbers, and move them
  differently on the next model; a line that is simply present cannot be
  skipped by any of them.
- The poisoned history was cleared, with the database backed up first so the
  deletion stays reversible.

- **EMMA can commission her own development.** She cannot change her own code —
  she is the process that is running — but she can record that a change is
  wanted and report on it afterwards. `core/tasks.py` holds the queue;
  `tools/development.py` gives her three tools over it: `request_development`,
  `work_status` and `answer_question`. These are the first tools ever
  registered on the agentic loop, and `core/router.py` did not change by a
  line — which is what the `Tool` protocol was built for in 0.1.0.
- A task passes through six stages (`new`, `understood`, `implemented`,
  `committed`, `pushed`, `deployed`) and parks on the user at four of them: the
  stage records what is done, the note asks permission for the step after it.
- **EMMA never speaks first.** Nothing notifies anybody: the developer leaves a
  question in the queue, the user sees it when they ask for the state of play,
  and the answer travels back the same way. One voice, no second bot.
- `dev_heartbeat` records when a development session last read the queue.
  There is no service behind it — only a session someone left open — so a dead
  session would otherwise pile up requests in silence; `work_status` reports
  the gap instead.
- The queue shares the database file with the conversation history, so the
  integrity check, the snapshots and the consistent backup already built around
  that file cover it too.
- `tests/test_tasks.py` and `tests/test_tools_development.py`: 39 tests
  covering the handover between user and developer, the full journey through
  every checkpoint, the staleness warning, and the two stores sharing a file.
- `scripts/task-queue.sh`: the only command a dedicated SSH key is allowed to
  run on the server, pinned through `command=` in `authorized_keys`. The
  development session polls the queue constantly and unattended, so giving it
  the administrative key would put the most powerful credential on the machine
  into the one path nobody watches. It accepts seven fixed verbs and no SQL,
  builds every query itself, and checks each value as an integer or matches it
  against a fixed list. A stolen development key can write nonsense into the
  queue — which the user reads — and nothing else.
- `scripts/queue-brief.sh`: a `SessionStart` hook that reports how many
  commissioned jobs are waiting. Nothing runs the queue on its own, so a
  request left overnight sits there until somebody thinks to look; this makes
  noticing automatic. It reports a count and not the requests themselves —
  reading them costs context in every session, including the ones that never
  touch them — and stays silent, exiting successfully, when the queue is empty
  or the server is unreachable.
- `scripts/task-queue.sh` gains `create`, for a defect found while working on
  the code, which would otherwise live only in a developer's memory. It moves
  nothing about who decides: a job opened this way still stops at the first
  checkpoint and asks. Jobs that originate with the user still arrive through
  EMMA.
- `scripts/watch-tasks.sh`: waits on the queue from the development machine and
  exits the moment there is work. The waiting is done by a shell loop, which
  costs nothing, so the session only wakes when there is something to do. The
  destination is an SSH host alias, never an address, so nothing about the
  machine reaches the repository.
- The design, and what was deliberately left out of it, is entry 17 of
  `REVISIONE.md`.

### Changed

- `prompts/system_prompt.txt`: describes the new tools and when to use them —
  never on her own initiative, either the user is explicit or she asks first.
  It also no longer claims she forgets past conversations, which stopped being
  true in 0.2.0.
- `CLAUDE.md` rule 8: report and ask between the stages of a piece of work
  rather than chaining them. Full permissions on the machine waive the prompt
  that nobody can click, not the asking itself.

- **The backup now always happens.** `scripts/backup.sh` prefers the second
  disk and falls back to `/var/backups/emma` on the system disk when there is
  none, instead of aborting. It says which it used, and why, in the log and in
  `MANIFEST.txt`; an archive beside the original protects against mistakes but
  not against that disk failing, so the compromise is stated rather than
  implied. An explicitly configured `BACKUP_DIR` is honoured as given and only
  falls back if it turns out to be unwritable.
- The default destination is used only when it genuinely is a separate
  filesystem, not merely when the directory exists. Writing into an unmounted
  `/mnt/backup` would appear to work while filling the system disk, and the
  archives would then vanish under the mount point the day the disk was
  attached, still occupying space nothing could account for.
- `emma-backup.service` no longer declares `RequiresMountsFor=/mnt/backup`,
  which turned a missing backup disk into a failed job — the opposite of the
  guarantee above. It creates the fallback directory with the right ownership
  via `ExecStartPre`, since `/var/backups` is root-owned and the service user
  could not create a subdirectory there on a fresh machine.
- `BACKUP_DIR` is now commented out in `.env.example`: leaving it unset lets the
  script choose, which is the better default.

### Added

- **Database integrity and self-healing.** `SqliteConversationMemory.open()`
  runs `PRAGMA integrity_check` before handing the database to the application.
  On failure the damaged file is moved aside — never deleted — and the newest
  snapshot that passes the same check is restored in its place; if none
  survives, the store starts empty. Every step is logged at `ERROR` level.
  Recovery is triggered *only* by verified corruption, never by a failed
  start-up from any other cause.
- Snapshots are written with `VACUUM INTO` on every successful open and clean
  shutdown, verified before they replace the previous generation. Two
  generations are kept (`emma.db.snapshot`, `.snapshot.prev`).
- `PRAGMA journal_mode=WAL`, which survives an abrupt kill far better than the
  default rollback journal.
- `tests/test_memory_sqlite.py`: 8 further tests covering recovery from a
  corrupt file, fallback to the older snapshot, quarantine instead of deletion,
  the no-snapshot case, and a stale write-ahead log.

### Fixed

- **`GroqLanguageModel` accepted a `tools` argument and ignored it**, so on the
  provider actually used in production the model was never even offered the
  tools it was supposed to call. The client dates from 0.1.x, when the tool
  list was empty and the omission cost nothing; 0.3.0 registered three tools
  and they were inert. Nothing failed and nothing was logged — EMMA simply
  answered in prose, which is indistinguishable from working unless you go
  looking for the tool call that never happened.
- The two dialects are now translated in both directions inside the adapter,
  where the difference belongs: declarations move from `input_schema` to a
  nested `function.parameters`; a request to run a tool moves between a
  `tool_use` content block and a `tool_calls` field whose arguments are a JSON
  string; a result moves between a `tool_result` block and a message of its own
  with `role: "tool"`. `core/router.py` still speaks one dialect and did not
  change.
- Replaying an agentic turn used to flatten tool traffic to prose, which left
  the model unable to see that it had ever called anything — so the second
  round of a tool turn started from nothing. It is now replayed intact.
- Malformed JSON in a tool call's arguments no longer drops the call, which
  would have looked like the model saying nothing at all; it runs with empty
  arguments and the tool reports the problem itself.
- `tests/test_llm_groq_tools.py`: 24 tests over the three translation
  functions, including a full round trip back out. Verified against the live
  API as well: the explicit prefix registers a task, a missing capability is
  proposed rather than registered, and a status question is answered from the
  database.
- **`systemd/emma.service` could not write the database at all.** The unit sets
  `ProtectSystem=strict`, which mounts the whole filesystem read-only, and
  declared no `ReadWritePaths`. On a clean install following the guide, 0.2.0
  therefore failed as soon as it tried to create `data/`; it only worked where
  the unit had been simplified by hand during deployment. The unit now declares
  `ReadWritePaths=/opt/emma/data` — the installation directory itself stays
  read-only, so the service still cannot rewrite its own code.
- **`systemd/emma-backup.service` could not read the database either.** The
  backup only reads, but a WAL reader has to update the `-shm` index beside the
  file, which `ProtectSystem=strict` forbade; the archive would have gone out
  without the history. Its `ReadWritePaths` now covers the database directory
  as well.
- **`scripts/backup.sh` mis-resolved an absolute `MEMORY_DB_PATH`.** It prefixed
  the project directory unconditionally, while `config.py` honours absolute
  paths, so a relocated database was reported as "no database file" and silently
  left out of the archive. The two now resolve paths the same way.
- **Snapshots were world-readable.** `VACUUM INTO` creates its target with the
  process umask, so a file holding the same conversations as the database came
  out `0644` next to a database at `0600`. They are now chmod'ed to `0600`
  before they are put in place, which matters wherever `MEMORY_DB_PATH` points
  at a directory less restrictive than the default.
- **Every nightly archive carried pip's HTTP cache.** The installation
  directory doubles as the home of the `emma` user, so `~/.cache/pip` lives
  inside it and `tar` swept it up: 26 MB of content reproducible from
  `requirements.txt`, in every archive, retained fourteen times over. Excluding
  it took a production archive from 23 MB to 340 KB.
- `SqliteConversationMemory.open()` raises a message naming `ReadWritePaths`
  when it cannot create the database directory, instead of an unhandled
  `OSError` that names neither the cause nor the fix.
- **`scripts/backup.sh` archived a live SQLite file with `tar`**, which can
  capture a half-written transaction: the archive reads back fine while the
  database inside it does not, and nothing detected this until a restore was
  attempted. The script now takes a consistent snapshot with `VACUUM INTO`,
  verifies it, and archives that instead; `data/` is excluded from the tar and
  `MANIFEST.txt` states whether the history is included and why not when it
  isn't. Requires the `sqlite3` package, which is documented in chapter 1 of
  the guide; without it the backup still succeeds, minus the history.

The next planned step is v0.3: real tools behind the existing `Tool` protocol.

## [0.2.0] - 2026-08-31

### Added

- **Persistent memory (SQLite)**: conversation history now survives process
  restarts. `SqliteConversationMemory` in `core/memory.py` stores messages in
  an SQLite file via `aiosqlite`; the sliding window and role-ordering invariant
  are enforced on every write, identical to the in-memory implementation.
- `MEMORY_DB_PATH` env var selects the database file (default `data/emma.db`).
- `aiosqlite==0.20.0` added to `requirements.txt`.
- `tests/test_memory_sqlite.py`: 9 tests including persistence-across-reopen.
- `data/` added to `.gitignore` (database file must never be committed).

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

### Documentation

- `docs/GUIDA.md`/`.pdf` brought in line with 0.2.0: multi-provider setup,
  persistent memory, `MEMORY_DB_PATH`, how to reset the conversation history,
  and the SQLite troubleshooting cases.

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

[Unreleased]: https://github.com/KurinY/emma/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/KurinY/emma/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/KurinY/emma/releases/tag/v0.1.0
