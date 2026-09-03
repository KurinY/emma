# REVISIONE.md — a critical review of the decisions

**This document is advisory.** The project was implemented exactly as
specified: none of the alternatives described here has been applied. You weigh
them and decide what to adopt; until you say so, what runs in production is the
specified version.

For each entry you will find: the decision as it was specified, whether I think
it is the best choice for *this* context (a self-hosted personal system, modest
hardware, a public and maintainable project, future evolution towards
SQLite/tools/voice), the concrete alternative, its pros and cons, and a verdict
among **a change to make now**, **worth considering in a future phase** and
**not worth it**.

---

## 0. Corrections and departures from the specification

No objective error in the specification stopped the system from working: I did
not have to correct anything to get it running. There are, however, four
departures to declare.

**0.1 — JARVIS renamed to EMMA.** On your explicit instruction during the work.
The name now appears in `emma.service`, `emma-backup.service`,
`emma-backup.timer`, in the project directory, in the `BACKUP_DIR` default
(`/mnt/backup/emma`) and in the personality prompt. No trace of the old name is
left in the code, the paths, the file names or the documentation.

**0.2 — `D:\JarvisBackups` became `D:\EmmaBackups`.** A direct consequence of
the point above: the specification named the first, but keeping a backup path
with the old name would have been inconsistent. It is a parameter in any case:
`-DestinationPath` overrides it without touching the script.

**0.3 — No `__init__.py` in `adapters/`, `core/` and `tests/`.** The required
structure listed the files one by one and did not include them, so I used
implicit namespace packages (PEP 420), which under Python 3.11 behave
identically with both `python main.py` and pytest (`pythonpath = ["."]` in
`pyproject.toml`). If you prefer explicit packages it is an addition of three
empty files and zero changes to the code.

**0.4 — The request/response objects live in `core/router.py`.** The
specification speaks of "a standard internal request object" but does not
provide a file to put it in. `AssistantRequest` and `AssistantResponse` are
therefore defined in `core/router.py` and imported from there by the adapters.
The alternative — a dedicated `core/models.py` — is discussed in entry 12.

---

## 1. The adapter pattern

**The decision as specified.** The router receives a standard internal request
object (text, user_id, conversation_id) and returns a standard response. No
Telegram import inside `core/`.

**Is it the best choice?** Yes, without reservation. It is the most important
decision in the project and the one that pays off most in the later phases: when
the voice satellite arrives, the router will not have to change by one line. The
cost today is minimal (two dataclasses and a ten-line conversion in the
adapter), the benefit is structural. In `adapters/telegram.py` the only point of
contact is the `_on_text_message` method, which builds the `AssistantRequest`
and consumes the `AssistantResponse`.

**The concrete alternative.** The only variant I would have considered is making
the response richer straight away, so as not to have to change it when voice and
tools arrive:

```python
@dataclass(frozen=True, slots=True)
class AssistantResponse:
    text: str                      # what is to be said or written
    degraded: bool = False
    attachments: tuple[Attachment, ...] = ()   # images, files, audio
    metadata: Mapping[str, Any] = field(default_factory=dict)  # latency, tokens, tools used
```

With `attachments` the Telegram adapter would already know how to send an image
and the voice one would know there is an audio file to play; with `metadata` you
could log cost and latency per message without polluting the text.

**In favour of the alternative.** It avoids an incompatible change to the
interface when you add tools that produce files or charts; it enables
per-message observability immediately.

**Against.** Today they would be permanently empty fields: complexity paid in
advance for a use case that does not yet exist, and `Attachment` would have to
be designed blind, without knowing which tools will really arrive. The
`degraded` field I did add is already earning its place (it distinguishes a real
answer from a courtesy message).

**Verdict: not worth it** now. Adding an optional field to a frozen dataclass is
a backwards-compatible operation: it will be done when the first tool needs to
return something that is not text.

---

## 2. A router already shaped as an agentic loop

**The decision as specified.** Use the Anthropic API's tool use with the full
loop (call → if `tool_use`, run and send back → repeat), even with an empty tool
list, and with a signature that allows future tools to be registered without
modifying the router.

**Is it the best choice?** Yes. It is the other decision that really pays:
writing the loop now costs thirty lines or so, rewriting it later would mean
redoing the tests and rethinking the memory. The signature `Router(llm, memory,
system_prompt, tools=(), max_tool_iterations=5)` accepts any object satisfying
the `Tool` protocol (`name`, `description`, `input_schema`, `async run()`), so
registering a tool is one line in `main.py`.

I added two protections the specification did not ask for but which the loop
makes necessary, and which I consider part of a correct implementation rather
than an extension: a ceiling on the number of rounds (`max_tool_iterations`),
because a model that keeps asking for tools would otherwise produce an unbounded
and billable sequence; and containment of tool exceptions, which are returned to
the model as a `tool_result` with `is_error`, so that a faulty tool does not
bring the turn down.

**The concrete alternative.** Use the official SDK's *tool runner*
(`client.beta.messages.tool_runner`), which implements the loop itself: you
register decorated Python functions and the SDK handles the call, the execution
and the resend.

**In favour of the alternative.** Less code of our own to maintain; the loop is
updated by Anthropic as the protocol evolves.

**Against.** It is in beta, so the interface can change underneath us in a
project that aims at stability; and above all it ties the *heart* of the system
to the SDK. Today `core/llm.py` is the only file that imports `anthropic`: if
one day you wanted to try a local model on your own hardware — perfectly
plausible for a self-hosted assistant — a second implementation of
`LanguageModel` would be enough. With the tool runner, the agentic loop itself
would be the SDK's property.

**Verdict: not worth it.** The hand-written loop fits in thirty lines, is tested
offline and buys us independence from the vendor. To be reconsidered only if the
tool-use protocol became much more complex than this.

---

## 3. The user whitelist

**The decision as specified.** The bot answers only the ID in
`TELEGRAM_ALLOWED_USER_ID` and silently ignores everybody else.

**Is it the best choice?** Yes, for v1. Silence is the right answer: a "you are
not authorised" would confirm to a stranger that the bot is alive and attended.
The numeric ID is not guessable and never changes, unlike the username. The
check is explicit in the handler (not a PTB filter) only so that I can log the
attempt at WARNING level: if somebody finds the bot, you see it in `journalctl`.

**The concrete alternative.** A multiple whitelist,
`TELEGRAM_ALLOWED_USER_IDS` as a comma-separated list, parsed in `config.py`
into a `frozenset[int]`, with the check becoming `user.id in allowed_ids`. Cost:
five lines.

**In favour of the alternative.** The day you wanted a family member to use the
assistant too, no code change would be needed, and the memory is already
isolated by `conversation_id` so the conversations would not mix.

**Against.** Real multi-user also means per-tool permissions (who can turn the
lights off? who can read the notes?) and a spending quota per user: a plural
whitelist would give the illusion of supporting several people without really
supporting their security model. Better to face it when the tools exist.

**Verdict: worth considering in a future phase**, together with the tools — not
before, because before then it is only a longer list with no semantics.

---

## 4. Configuration from `.env` only

**The decision as specified.** `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL`,
`TELEGRAM_BOT_TOKEN`, `TELEGRAM_ALLOWED_USER_ID`, `MAX_HISTORY_MESSAGES`,
`SYSTEM_PROMPT_PATH`, `BACKUP_DIR`, `BACKUP_KEEP`.

**Is it the best choice?** Yes. One file, one format, no hierarchy of sources to
explain in the guide. `config.py` validates everything at startup and fails with
a message naming the guilty variable, so a wrong `.env` is diagnosed at a glance
instead of on the first message.

Two observations about the set of variables as it stands.

`BACKUP_DIR` and `BACKUP_KEEP` are the only two the application never uses:
`scripts/backup.sh` consumes them, reading the `.env` on its own account.
`config.py` loads and validates them all the same, because the specification
lists them among the configuration variables and because this way a
`BACKUP_KEEP=zero` is discovered when the service starts and not at half past
three in the morning, when the timer fails silently.

I added no variables that were not asked for. The two I would have wanted most
often, and therefore propose here, are `LOG_LEVEL` (today fixed at INFO: to
debug a problem you have to edit `main.py`) and `ANTHROPIC_MAX_TOKENS` (today
fixed at 2048 in `core/llm.py`).

**The concrete alternative.** Replace the manual loading with
`pydantic-settings`:

```python
class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="forbid")
    anthropic_api_key: SecretStr
    anthropic_model: str = "claude-haiku-4-5-20251001"
    telegram_allowed_user_id: int
    max_history_messages: PositiveInt = 20
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
```

Declarative validation, checked types, a `SecretStr` that stops the key ending
up in a log or a traceback, `extra="forbid"` that reports a typo in a variable
name instead of ignoring it.

**In favour of the alternative.** Less hand-written code (`config.py` would go
from ~200 to ~60 lines), excellent error messages, and `SecretStr` is real
protection.

**Against.** One more dependency — though pydantic is already installed as a
FastAPI dependency, so the actual cost is only `pydantic-settings`. And a
hand-written file is more readable for somebody who comes to the project without
knowing pydantic, which matters for a public, didactic project.

**Verdict: worth considering in a future phase.** The right moment is when the
variables go from eight to fifteen (voice and tools will bring a fair few): at
that point declarative validation wins clearly. In the meantime I suggest only
adding `LOG_LEVEL`, which is the variable you will miss the first time something
behaves strangely in production.

---

## 5. Resilience: retry with backoff

**The decision as specified.** Retry with exponential backoff (3 attempts) on
API calls; if they all fail, a courtesy answer; never a crash, never silence.

**Is it the best choice?** In substance yes: three attempts with waits of 1s and
2s absorb a network blip or a 529 overload without you noticing, and the
courtesy message with the process still alive is exactly the right behaviour for
a home assistant. I disabled the SDK's internal retries (`max_retries=0`)
because otherwise the real number of attempts would have been 3×3 = 9, with
multiplied waits and unreadable logs.

There is one point I would have done differently, and it is the only one in the
whole specification on which I have a concrete technical objection: **today even
errors that cannot succeed on a second attempt are retried.** A wrong API key
returns 401, and the current code retries it three times, waiting three seconds
before answering you. It works (the acceptance criterion is met: you get the
courtesy message and the process stays alive) but it is three seconds and three
calls wasted on an error whose answer is already certain.

**The concrete alternative.** Distinguish retryable errors from final ones in
`core/llm.py`:

```python
RETRYABLE = (
    anthropic.APIConnectionError,   # no network, DNS, TLS
    anthropic.APITimeoutError,
    anthropic.RateLimitError,       # 429
    anthropic.InternalServerError,  # 5xx
    anthropic.OverloadedError,      # 529
)

except anthropic.AnthropicError as exc:
    if not isinstance(exc, RETRYABLE):
        raise LLMUnavailableError(f"final error: {exc}") from exc
    ...backoff and retry...
```

I would also add jitter (`delay * random.uniform(0.8, 1.2)`), pointless with a
single client but good practice, and honour the `retry-after` header on 429s,
which the API sends and which is more reliable than any backoff we compute
ourselves.

**In favour of the alternative.** An immediate answer when the error is
configuration, clearer logs (`AuthenticationError` once instead of three
identical lines), no wasted call to an endpoint that has already said no.

**Against.** A list of classes to keep in step with the SDK versions: if
Anthropic introduces a new transient error and we do not add it, it is treated
as final and we lose a legitimate retry. The current behaviour, "retry
everything", is the simplest and always errs on the cautious side.

**Verdict: a change to weigh now** — it is the only entry in this document I
would put at the top of the list. It is not a bug, it is a small inefficiency,
but the code is already written to accommodate it (an `isinstance` condition
inside the existing `except` is enough) and it improves both perceived latency
and log readability. Say the word and I will apply it.

---

## 6. Memory behind an interface

**The decision as specified.** An abstract interface (`get_history` / `append` /
`prune`) and an in-memory implementation with a sliding window on
`MAX_HISTORY_MESSAGES`, to be replaced in future with SQLite without touching
the router.

**Is it the best choice?** Yes for v1, and the interface is sized well: three
methods, none of them surplus. I made two implementation choices worth
declaring, because they were not in the specification:

- **The methods are `async`.** A synchronous memory would have been simpler
  today, but SQLite (with `aiosqlite`) and any other storage are asynchronous:
  having them already `async` is precisely what makes the promise "I replace it
  without touching the router" true.
- **The window never stops on an `assistant` message.** If the cut would leave
  the history beginning with an assistant answer, one further message is
  dropped. The Messages API rejects a conversation that does not begin with the
  user: without this rule, an odd `MAX_HISTORY_MESSAGES` would have broken the
  system at random after a few exchanges.

**The concrete alternative — and this is the real question: how I would do
persistence.**

I would use **plain SQLite through `aiosqlite`**, with no ORM. The reasons: the
schema is two tables, SQLAlchemy would bring a layer of abstraction and ~30 MB
of dependencies in order to save twenty lines of SQL, and on a modest server
SQLite in WAL mode handles one person's traffic without effort.

The schema:

```sql
CREATE TABLE conversations (
    conversation_id TEXT PRIMARY KEY,
    channel         TEXT NOT NULL,          -- 'telegram', tomorrow 'voice'
    created_at      TEXT NOT NULL,
    last_active_at  TEXT NOT NULL,
    summary         TEXT                     -- a summary of the past, see below
);

CREATE TABLE messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL REFERENCES conversations(conversation_id),
    role            TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content         TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    tokens          INTEGER                  -- to measure the real cost
);

CREATE INDEX idx_messages_conversation ON messages(conversation_id, id DESC);
```

`PRAGMA journal_mode=WAL` and `PRAGMA synchronous=NORMAL`: reads and writes do
not block each other and there are few fsyncs.

`SqliteConversationMemory` would implement the same interface: `get_history` is
a `SELECT ... ORDER BY id DESC LIMIT ?` reversed in Python; `append` is an
`INSERT` plus the update of `last_active_at`; `prune` **would delete nothing** —
and that is the important conceptual difference.

On managing the context window my proposal is **truncation now, summaries later,
and never deletion**:

1. The history stays intact on disk forever (it is the reason one adds a
   database: to be able to search for "what did we say in March").
2. `get_history` returns only the last *N* messages, exactly as today: that is
   the truncation, and for 95% of household conversations it is enough.
3. When a conversation exceeds a threshold (say 40 messages beyond the window),
   a periodic job asks the *cheapest* model for a 200-word summary of what falls
   outside the window and stores it in `conversations.summary`. The router
   prepends it to the system prompt as "Context from earlier conversations:
   ...". It costs one Haiku call now and then and gives the very convincing
   illusion of an assistant that remembers.
4. The summary is regenerated from the previous summary plus the new messages
   that have left the window, so the whole history is never re-read.

Backup: the `.db` file goes in the project directory, so `backup.sh` already
picks it up as it is — but with WAL active the only correct way to copy it hot
is `sqlite3 emma.db ".backup /path/copy.db"`, not `cp`. When the database
arrives, that will be the only line to add to the script.

**In favour of the alternative.** Conversations survive restarts (today a
`systemctl restart` wipes everything); searching the past becomes possible; the
`tokens` field tells you what you really spend.

**Against.** Schema migrations to handle by hand; one more file to back up and
restore correctly; and persistent memory brings with it a privacy question that
does not exist today (everything you say stays written on disk in the clear).

**Verdict: to do in the v0.2 phase**, as the roadmap already plans. It is the
natural next step and the interface is ready to receive it.

---

## 7. Structured logging to stdout

**The decision as specified.** Level, timestamp and event to stdout, so that it
ends up in journalctl via systemd.

**Is it the best choice?** Yes. Writing to stdout and letting the infrastructure
decide where the logs end up is the right practice: no files to rotate, no
permissions to manage, and `journalctl -u emma -f` gives you everything. The
current format is `timestamp | LEVEL | logger | message`, readable at a glance
and greppable.

**The concrete alternative.** JSON logs, one per line, with `structlog` or a
twenty-line custom formatter:

```json
{"ts":"2026-08-29T14:03:11+02:00","level":"info","event":"message_handled",
 "conversation_id":"12345","chars_in":34,"chars_out":180,"duration_ms":1240,
 "tokens_in":420,"tokens_out":95}
```

With journald one could go further and use the native structured fields
(`systemd.journal.JournalHandler`), queryable with `journalctl
CONVERSATION_ID=12345`.

**In favour of the alternative.** It becomes possible to answer questions like
"how much did I spend this week" or "what is the median latency" with a `jq`
instead of by eye; and the day there are several channels, filtering by channel
will be trivial.

**Against.** JSON is far less readable when you are watching the logs live to
understand why the bot is not answering — which is 90% of the times you will
look at them. And a metrics dashboard for a single-user system is oversized.

**Verdict: not worth it** while there is only one user. It becomes interesting
when the tools exist and you want to know which ones you really use: at that
point I suggest the compromise of a second "events" logger in JSON to a separate
file, leaving the operational log readable as it is.

---

## 8. The language of the code and the documentation

**The decision as specified.** Code, comments and docstrings in English,
docstrings on all public functions; `GUIDA.pdf` in Italian; the README and other
project files in English.

**Is it the best choice?** The rule "one language per audience, not one language
per file" was the right one. It has since been superseded by a simpler rule, and
the change is worth recording because it reverses part of this entry.

Originally I applied the specification to the letter with one declared
exception: **`REVISIONE.md` was in Italian**, on the grounds that it is a
decision document addressed to you, tied to a specification written in Italian,
and that for the community it would be noise. I noted then that it was a project
file and that the specification would have wanted English, and offered to
translate it.

**On 3 September 2026 the owner asked for exactly that**: everything written in
the repository in English, `docs/GUIDA.md`/`.pdf`, this file and `SESSIONS.md`
included, with the conversation between us staying in Italian. The reasoning is
sound and simpler than mine: a repository that is public reads the same way to
everybody who finds it, and "one language per audience" quietly becomes "one
language per author's mood" as soon as the audiences overlap — which they do,
because the person maintaining the instance and the person reading the design
review are the same person here.

Two things stayed in Italian, deliberately, and they are not documentation:

- `prompts/system_prompt.txt`, EMMA's personality. She speaks Italian to her
  owner. The file is configuration.
- The strings the tools return (`"Registrato come fatto #1."` and the rest).
  They are what the user reads on Telegram, and the tests assert them
  character by character.

Where a document quotes one of those, the quotation stays in the original with
an English gloss beside it. A translated quotation is a false one — it claims
the program said something it never said.

**The concrete alternative** (unchanged, and still not taken). Externalise every
user-facing string into a file (`prompts/messages.it.txt`, or a dictionary in
`config.py`), so that the assistant's language becomes configurable alongside
the personality.

**In favour of the alternative.** Total consistency (zero Italian in the code)
and an English-speaking contributor could use the project without touching
`core/`.

**Against.** A handful of strings do not make an internationalisation system,
and the indirection would make it harder to see what happens when reading the
router.

**Verdict: worth considering in a future phase**, if and when somebody really
uses the project in another language. Before then it would be abstraction for
its own sake. Rule 6 of `CLAUDE.md` now records the language policy, so that a
future session does not "tidy up" by translating the personality.

---

## 9. Component: the channel (Telegram, long polling)

**The decision as specified.** `python-telegram-bot` in long polling, never a
webhook, no exposed port.

**Is it the best choice?** Yes, and for a home server behind NAT it is not
really a contest. A webhook would need a public IP or a tunnel, a domain, a TLS
certificate, renewals, and a port open to the internet on a machine at home: all
attack surface in exchange for a few hundred milliseconds less latency. Long
polling pays only for one permanently open outbound connection, which is
irrelevant.

I added `drop_pending_updates=True` at startup: after a restart you get a live
assistant, not a burst of answers to questions from three hours ago. And
`allowed_updates=[Update.MESSAGE]`, which cuts useless traffic.

**The concrete alternative.** If latency ever really mattered, the webhook could
be done *without* opening ports: an outbound tunnel (Cloudflare Tunnel or
Tailscale Funnel) exposing the FastAPI endpoint that already exists. It would
need a `POST /telegram/webhook` handler with `secret_token` verification, and
`Application.process_update()` in place of the `Updater`.

**In favour.** Lower latency and no polling that comes back empty.

**Against.** One more external dependency (the tunnel) at exactly the point
where the project wants to be self-sufficient, and a public endpoint to protect.

**Verdict: not worth it.** Long polling is the correct choice for this system
and will remain so with voice and tools.

---

## 10. Component: the project structure

**The decision as specified.** The file tree given: flat modules at the root
(`main.py`, `config.py`), `adapters/`, `core/`, `prompts/`, `scripts/`,
`systemd/`, `tests/`, `docs/`.

**Is it the best choice?** For a project of this size yes: you open the folder
and see where everything is without navigating. The `adapters/` ↔ `core/`
separation is the one that counts, and it is there.

**The concrete alternative.** The `src/` layout, standard for published Python
projects:

```
src/emma/{__init__,config,main}.py
src/emma/{adapters,core,prompts}/...
tests/
pyproject.toml          # with [project] and a build backend
```

installed with `pip install -e .`, absolute imports `from emma.core.router
import Router`, an `emma` entry point instead of `python main.py`.

**In favour of the alternative.** It becomes impossible to import the source
tree by mistake instead of the installed package; namespaced module names (today
a root `config.py` could in theory collide with something on `sys.path`);
publishable on PyPI; it is what an experienced Python contributor expects.

**Against.** It adds an installation step and a directory level to a project
that is deployed with `git pull` and read in half an hour. For a self-hosted
assistant, "clone and run" is worth more than formal correctness.

**Verdict: worth considering in a future phase**, and precisely on the day you
wanted to distribute EMMA as an installable package. If it stays a project to be
cloned, the current structure is better.

On the minor point 0.4 (where `AssistantRequest` and `AssistantResponse` live):
a dedicated `core/models.py` would be slightly cleaner, and I would recommend it
the moment the response grew richer (entry 1) or other shared types appeared.
With two dataclasses, keeping them next to the router that uses them is more
readable than splitting them into a file with three useful lines.

---

## 11. Component: resilience at the system level

**The decision as specified.** The main service (`emma.service`) with
`Restart=always`.

**Is it the best choice?** Yes, and it is the right level: the process should
not try to survive itself, systemd takes care of that. I added `RestartSec=5s`
and `StartLimitBurst=5` in `[Unit]`: if the service dies five times in five
minutes the cause is not transient (a wrong `.env`, for instance) and carrying
on restarting would only hammer the Telegram API. Better to stop and be noticed.

I also hardened the systemd sandbox (`ProtectSystem=strict`, `ProtectHome`, an
empty `CapabilityBoundingSet=`, `RestrictAddressFamilies`, `SystemCallFilter`):
the process can read its own directory and open outbound HTTPS connections, and
nothing else. This is worth knowing because when you add a tool that writes a
file you will have to add a `ReadWritePaths=`, or you will discover it through a
non-obvious `Permission denied`.

**The concrete alternative.** An active watchdog: `Type=notify` with
`WatchdogSec=120`, and in the code a task that calls `sd_notify("WATCHDOG=1")`
only after verifying that Telegram polling is really alive. Today a process that
stays up but stops receiving updates (a bug in the updater, a hung connection)
would not be restarted: from systemd's point of view it is perfectly well.

**In favour.** It covers the only realistic way EMMA can "die without dying".

**Against.** A dependency on `python-systemd`, and a badly tuned watchdog
restarts the service while it is merely waiting for a slow answer from the model
— a cure worse than the disease.

**Verdict: worth considering in a future phase**, if it really happens that the
bot stops answering while the service is *active (running)*. Before then it is
speculative complexity. A poor but immediate substitute: the `/health` endpoint
already exposed on loopback, queryable from a cron job that restarts the service
if it does not answer.

---

## 12. Component: the FastAPI + uvicorn stack

**The decision as specified.** FastAPI + uvicorn, a single asynchronous process,
with Telegram polling in the same event loop.

**Is it the best choice?** With reservations. FastAPI does almost nothing here:
v1 exposes a single `/health` endpoint on loopback, and `python-telegram-bot`
already knows how to run its own event loop (`Application.run_polling()`). The
cost is three dependencies (fastapi, starlette, uvicorn) and a startup structure
— lifespan, `uvicorn.run` — more elaborate than necessary.

That said, it is not a wrong choice: it is a bet on the future with a good
chance of paying for itself. The Raspberry Pi satellite will have to talk to the
central node over HTTP, and then the server will already be there; a web control
panel, if you ever want one, likewise. And `/health` is not useless: it is the
simplest way to know the process is alive without reading the logs.

**The concrete alternative.** `main.py` with no HTTP server:

```python
def main() -> int:
    config = load_config()
    application = build_telegram_application(config)
    application.run_polling(allowed_updates=[Update.MESSAGE])
    return 0
```

Three fewer dependencies, thirty fewer lines, and startup becomes trivial. When
HTTP is needed, FastAPI is added at that moment.

**In favour of the alternative.** Less surface, less to update, less to explain
in chapter 3 of the guide. On modest hardware, a few tens of MB less RAM too.

**Against.** Redoing startup when the satellite arrives, and losing `/health`.

**Verdict: not worth changing now.** I wrote it because the specification asks
for it and because the bet is reasonable: the voice satellite is on the roadmap,
it is not hypothetical. But if you decided that v2 will have no HTTP satellite,
this is the first simplification to make.

---

## 13. Component: backup and versioning

**The decision as specified.** Git for versions, `backup.sh` + a systemd timer
for dated archives with rotation, `backup-dev.ps1` for snapshots on Windows, the
flow PC → GitHub → `git pull` on the server.

**Is it the best choice?** Yes, and the two levels complement each other in the
right way: Git protects against editing mistakes, the archives protect against
disk failure and preserve the `.env`, which cannot live in Git. I added three
things I considered part of "done properly":

- **verifying the archive before the rotation** (`tar -tzf`), so that a failed
  backup can never delete yesterday's good one;
- **a `MANIFEST.txt` inside the archive** with the date, the host and the Git
  commit it came from — an archive without provenance is hard to use when it is
  really needed;
- **reading the `.env` without `source`**: `source .env` would execute the
  file's contents, and a data file must never be executed.

**The concrete alternative.** Replace `tar` with **restic** or **borgbackup**:
deduplicated incremental backups, encrypted, with built-in verification (`restic
check`) and restoration of individual files from any snapshot.

```bash
restic -r /mnt/backup/emma-repo backup /opt/emma --exclude .venv
restic -r /mnt/backup/emma-repo forget --keep-daily 14 --prune
```

**In favour of the alternative.** Encryption at rest (today the archive holds
your API key in the clear on a disk that could be stolen or resold); far less
space, because 14 nearly identical copies are deduplicated; the ability to add a
remote destination (S3, Backblaze) with the same command line, which you do not
have today — if the house burns down, the backups burn with the server.

**Against.** An external dependency to install and update; one more passphrase
to keep safe (and if you lose it, you have lost the backups); and a `tar.gz` can
be opened by anyone, anywhere, in ten years' time, without having restic
installed — which for a backup is an underrated quality.

**Verdict: worth considering in a future phase**, with one priority: the part
that convinces me most is not the deduplication, it is **the copy that lives
away from the house**. Even keeping `tar.gz`, syncing `BACKUP_DIR` to an
external disk you unplug, or to remote storage, would cover the one scenario
that leaves you exposed today. With encryption becoming mandatory the moment the
archives leave the house.

---

## 14. Component: `CLAUDE.md`

This entry answers, point by point, the questions you asked.

**The decision as specified.** A `CLAUDE.md` at the root with the permanent
instructions for any AI assistant on the project: after every session run
`backup-dev.ps1` and make descriptive commits; never touch `.env`; never apply
unrequested architectural changes, deferring them to `REVISIONE.md`.

**Is it the best choice?** Yes, and it is the subtlest idea in the whole
specification: embedding the discipline in the project instead of in your
memory. A file the assistant reads by itself at the start of every session is
the only way a rule survives sessions that share no memory.

**Are the instructions clear, or open to divergent readings?** The
specification's three rules were clear in intent but vague at the edge, and
ambiguity is where sessions diverge. I wrote them trying to eliminate it:

- *"never touch `.env`"* could mean "do not modify it" or "do not even read it".
  I wrote both explicitly, plus the ban on removing it from `.gitignore` and on
  pasting real secrets into code, tests, documentation or commits.
- *"architectural changes"* is the most dangerous phrase: with no definition,
  one session sees renaming a function in it and freezes, another lets a change
  of storage through. I put in a closed list: module boundaries, the
  `ConversationMemory` and `Tool` interfaces, the shape of the request/response
  objects, the storage backend, the `.env` variables, the dependencies, the
  process model, the deployment layout. Outside that list, you proceed.
- *"commits with descriptive messages"* is a subjective criterion. I put in a
  complete example of a good message and a list of rejected ones (`update`,
  `fixes`, `wip`).

**Are they too rigid, or too vague?** The greater risk was rigidity on rule 2:
taken literally, "do not make architectural changes" would block a necessary fix
too. I put in two release valves: the exception for an objective error that
stops the project working (with an obligation to report it at the top of
`REVISIONE.md`), and the general clause under which an explicit instruction from
you takes precedence, provided the assistant declares which rule it is setting
aside. Without that clause, sooner or later you would find an assistant refusing
to do what you have just asked, citing a file.

On the opposite side — too vague — the backup rule was the weakest: "run
`backup-dev.ps1`" does not say what to do if it fails. I added the explicit
order (verify → snapshot → commit → report) and the obligation to declare the
failure instead of skipping the step in silence, which is the typical way this
rule degrades.

**Is there redundancy or contradiction with README/CONTRIBUTING?** Minimal and
deliberate redundancy on one point only: "architectural decisions are discussed
first" is in both `CONTRIBUTING.md` and `CLAUDE.md`, because the two files have
different readers (a human contributor, an assistant) and neither necessarily
reads the other. There are no contradictions: I kept the code style in
`CONTRIBUTING.md` only, and `CLAUDE.md` points at it instead of repeating it, so
the two cannot diverge. The one overlap to watch is the language (both declare
it): if it changes, they have to change together — and that is why I put the "if
you change X, update Y" table into `CLAUDE.md`.

**Will it still hold as the project grows?** Largely yes. Rules 1 (secrets), 3
(backup and commit) and 4 (do not delete blindly) are independent of the phase.
The two that will age are:

- **rule 2**, whose definition of "architectural" is calibrated on v1: when the
  tools exist it will have to say whether adding a tool is an architectural
  change (in my view no, if it satisfies the `Tool` protocol; yes, if it changes
  the protocol);
- **the table in rule 5**, which lists the documents to update and will grow
  with the project.

Both are maintenance of a few lines, anticipated by the file's structure.

**The concrete alternative.** The version I would write if I had to start again,
at a more mature stage, is a **short** `CLAUDE.md` — half a page with the
invariant rules only — plus a `.claude/` directory with specialised instructions
loaded only when needed:

```
CLAUDE.md                        # 5 rules, ~40 lines: secrets, architecture,
                                 # backup+commit, do not delete, document
.claude/skills/add-skill.md      # how a tool is added to the router, with an example
.claude/skills/release.md        # version bump, CHANGELOG, tag, push
.claude/skills/deploy.md         # backup on the server, pull, restart, verify
```

**In favour of the alternative.** A long instruction file degrades: the more
rules it holds, the less weight each one carries, and the last are followed
worse than the first. Keeping the core invariant and moving the procedures into
files loaded on demand keeps adherence high and makes the procedures more
detailed at no cost.

**Against.** More files to keep in step; and while the project is small, a
single file you read in one breath is more honest and easier to check.

**Verdict: worth considering in a future phase** — when the tools arrive, that
is, when there are repetitive procedures deserving a card of their own. For v1
the single file is the right choice; I wrote it with numbered headings precisely
so that splitting it, when the time comes, is mechanical.

---

## 15. A note on licences for the future phases

The project is MIT and the current dependencies are all compatible: `anthropic`
(MIT), `fastapi` (MIT), `uvicorn` (BSD-3), `python-dotenv` (BSD-3),
`python-telegram-bot` (**LGPL-3.0**), `ruff` (MIT), `pytest` (MIT). No problem
today, but two things to keep an eye on.

**`python-telegram-bot` is LGPL-3.0.** Using it as a library, importing it
without modifying it, does not infect your code: you stay MIT. The real
constraints: if you distributed EMMA as a binary or a container with the library
inside, you must let the user replace it with another version (with `pip` this
is automatic); and if you **modify** the library, your modifications have to be
released under LGPL. For a project distributed as source on GitHub, in practice
nothing changes.

**The voice phase — here the question is concrete.** The typical candidates:

- **Piper** (TTS): licensed **MIT** — no problem. The *voices*, however, have
  licences of their own, often CC BY-SA or derived from datasets with
  restrictions: they have to be checked one by one and must not be bundled into
  the repository without checking, because a CC BY-SA voice requires attribution
  and share-alike.
- **whisper.cpp** (STT): MIT; OpenAI's Whisper models are MIT.
- **openWakeWord** (wake word): Apache-2.0; here too the individual word models
  have their own licences.
- **Beware of Coqui TTS**: **MPL-2.0** for the code, but some pre-trained models
  have a non-commercial licence (CPML) forbidding commercial use. Fine for
  personal use, but not to be distributed with the project as if it were MIT.
- **eSpeak NG**, if you used it as a fallback, is **GPL-3.0**: invoked as an
  external process it infects nothing, but it must not be linked or included.

**A practical rule** for when the voice phase arrives: no model, voice or weight
inside the repository. They are downloaded on first start by a script that
prints the licence of what it is downloading, and they are documented in a
`THIRD_PARTY.md`. That way the repository stays purely MIT and the user knows
what they are installing.

---

## 16. SQLite database integrity — **implemented on 31 August 2026**

> **Status.** 16.1 and 16.2 were implemented at your request, together with a
> limited form of self-restoration. 16.3 remains the verdict on the *generic
> automatic mirror*, which was not implemented — the difference is explained in
> 16.5, added after the implementation.

**Where it comes from.** A question of yours: is it worth backing up the
database on its own, and automatically restoring the good copy if the service
does not come back up? The answer is half yes and half no, and looking into it
turned up a real defect in the current backup.

### 16.1 — The defect: `tar` copies a live database

`scripts/backup.sh` archives the whole project directory while the service is
running. `data/emma.db` is read page by page while EMMA writes to it: if a
`COMMIT` lands halfway through the read, the archive contains an internally
inconsistent database. The verifying `tar -tzf` does not notice — it checks that
the archive is readable, not that the `.db` inside is valid.

The practical consequence: the archives produced so far contain reliable code
and `.env`, and a `.db` that might not open. You would find out only on the day
of the restore.

**The fix.** SQLite has the mechanism for exactly this. Before the `tar`, in
`backup.sh`:

```bash
if [[ -f "${PROJECT_DIR}/data/emma.db" ]]; then
    sqlite3 "${PROJECT_DIR}/data/emma.db" \
        "VACUUM INTO '${STAGING}/emma.db.snapshot'" \
        || log "warning: could not snapshot the database, continuing without it"
fi
```

and `data/` is excluded from the `tar`, with the snapshot archived in its place.
`VACUUM INTO` produces a consistent copy of a database that is in use, without
stopping the service. It adds one dependency: the `sqlite3` package, which is in
the official repositories.

Cost: six lines and a package. Benefit: backing up the data becomes reliable
instead of probable. **Verdict: to do now.**

### 16.2 — WAL and an integrity check at startup

Two improvements that stand on their own.

`PRAGMA journal_mode=WAL` in `SqliteConversationMemory.open()`: the write-ahead
log survives a brutal interruption (kill -9, the OOM killer, a power cut) far
better than the default journal. One line, no disadvantage in this
single-writer scenario.

`PRAGMA integrity_check` at the same place: if the database is corrupt, EMMA
moves it to `emma.db.corrupt-<timestamp>`, creates a new empty one, logs at
ERROR level where it put the file, and starts again. **It restores nothing by
itself:** it tells you what happened, keeps the evidence and comes back into
service. The decision about what to recover stays yours.

Cost: ten lines or so in `open()` and a couple of tests. **Verdict: to do now,
together with 16.1.**

### 16.3 — The automatic mirror: why not

The idea was: if the service does not come back up, put the last good copy of
the database back in place. Three objections.

**The diagnosis would nearly always be wrong.** The real reasons EMMA does not
start are, in order of frequency: an incomplete or malformed `.env`, a missing
dependency after an update, a code error just deployed, a path that is not
writable. A corrupt database does not even make the list. An automatic restore
in all those cases throws away the recent conversations without solving
anything, and replaces a diagnosable error with inexplicable behaviour.

**Corruption is extremely rare with this usage profile.** A single process, a
single connection, explicit commits, no write concurrency: it is the safest
possible configuration for SQLite. For it to become corrupt you need a physical
disk failure or a kernel panic inside an `fsync`. With WAL active (16.2) even
that window narrows.

**The value of the data does not justify the machinery.** The memory is a
sliding window of twenty conversation messages. Losing it costs the recent
context, not an archive with legal or accounting value. An automatic recovery
system is code that runs unsupervised at the worst possible moment — startup
after a fault — and is therefore more capable of causing damage than the damage
it prevents is serious.

**Verdict: not worth it.** Detection (16.2) gives ninety per cent of the benefit
with ten per cent of the risk. Detecting and reporting is the right behaviour;
restoring on one's own is not.

### 16.5 — What was implemented, and why it does not contradict 16.3

You asked that EMMA should still be able to restore herself "as far as
possible". The implementation accepts the request without giving up the
objection, because the boundary is **what triggers the restore**, not the
restore itself.

**What was done.** On opening, EMMA verifies the database with `PRAGMA
integrity_check`. If the check fails — so with an established diagnosis, not a
supposed one — it moves the broken file to `emma.db.corrupt-<date>`, restores
the most recent snapshot that passes the same check, and if that one is
unreadable too, tries the previous generation. If nothing healthy exists, it
starts empty. All at ERROR level in the logs.

**What is still not done**, and is the point of 16.3: no restore is triggered
because *the service did not start*. An incomplete `.env`, a missing dependency,
a code error just deployed do not even reach this code — they fail earlier, with
their own error message intact. It is the distinction that makes the difference
between a repair and a cover-up.

The rule in one line: **you restore on a diagnosis, never on a symptom.**
`integrity_check` is a diagnosis; "it does not start" is a symptom with a dozen
different causes, of which database corruption is the least likely.

**Accepted data loss.** The restore returns to the state of the last snapshot,
written at the last start or the last clean shutdown. Messages exchanged after
that moment are lost, and the log says so explicitly. The window could be
narrowed with a periodic snapshot (an async task in the lifespan, or a systemd
timer): that is the right lever if one day the window turned out to be too wide,
and it does not require touching the recovery logic.

### 16.4 — If one day the data became important

If in a future phase EMMA kept notes, reminders or data you cannot reconstruct,
the correct answer would still not be the automatic mirror, but: hourly
snapshots with `VACUUM INTO` instead of daily ones, a copy away from the house
(already entry 13), and a documented explicit restore command. The frequency and
the destination are the right levers; automatic recovery remains the wrong one.

---

## 17. EMMA as the commissioner of her own development (proposal, 31 August 2026)

**Where it comes from.** Your idea: *"vorrei che EMMA utilizzasse le tue
capacità di scrittura di codice per implementarsi dall'esterno"* — that EMMA
should use my code-writing abilities to implement herself from the outside. Not
a parallel work channel — I had proposed that and I was wrong — but EMMA herself
as the commissioner: you ask her for a capability she does not have, she records
it, I implement it, she restarts having it.

EMMA does not modify herself: she is the running process, she cannot rewrite
herself from under her own feet. But she can **commission her own evolution and
receive it**.

This entry is the design agreed in conversation. Nothing is implemented.

### 17.1 — The constraints that define it

They are yours, and they are the ones that ruled out the alternatives:

| Constraint | What it excludes |
| --- | --- |
| One bot only, EMMA | the separate work channel |
| **For now no API key: Claude Code open on the development PC** | every headless variant, or one installed as a service |
| No additional spending | polling with the model running |
| EMMA never speaks first | every push notification |
| Permission at every step | autonomous execution all the way through |
| Full permissions on the machine | local confirmation prompts |

The last two seem to contradict each other and do not: they act on different
planes. No blocking **on the machine**, where you have no physical access and
could not answer; explicit consent **in conversation**, where the phone is
enough.

**No metered key on the development side, for the time being.** The executor is
an interactive Claude Code session open on the development PC, running on the
subscription already in use. No `ANTHROPIC_API_KEY` for generating code, no
daemon, no service to install, nothing running on the VPS beyond EMMA. So the
following stay outside **this first version**:

- headless Claude Code on the production VPS;
- a systemd service launching autonomous work;
- any execution that consumes a paid key.

It is a choice about *when*, not about *whether*: it can change, and the design
is built so that changing it does not cost a rewrite (17.1.1).

One part, though, does not depend on payment. The development PC is **today the
only place where the work can actually happen**: that is where the repository
with its history lives, where git is configured, and where GitHub is reachable —
which the VPS is not, being IPv6-only. Even paying, an executor on that server
could not push. The server hosts EMMA; the PC hosts the workshop.

While this arrangement holds, two consequences:

- **If the session is not open, nothing happens.** There is no process
  collecting jobs in its absence. The PC switched on with a live session is part
  of the architecture, and turning it off is the master switch.
- **The consumption is the subscription's**, counted in session usage and not in
  billed tokens. That is exactly why 17.4 exists: not to save money on a bill,
  but to avoid burning the session on empty wake-ups.

Hence also the correct reading of the *"spending on paid APIs"* gate among the
four you chose: as things stand it concerns **EMMA**, that is, a possible move
from Groq's free tier to the metered Anthropic APIs. The development side, for
now, has no key to spend; if one day it has, that gate will cover it too.

### 17.1.1 — What would change if one day it became paid

It is worth fixing this now, because it is what keeps the door open: the design
is **neutral with respect to the executor**. The queue, the checkpoints, EMMA's
tools and the way the questions reach you do not know who is working at the
other end.

| Piece | If the executor changes |
| --- | --- |
| the `tasks` table and the state machine | unchanged |
| EMMA's tools (17.6) | unchanged |
| checkpoints 1/3/4/5 and their meaning | unchanged |
| the zero-cost wait (17.4) | unchanged as an idea, what changes is who runs it |
| who collects and works | the only piece that gets replaced |

So one thing only would change: from "an open session that wakes up" to "an
executor that runs by itself". The main risk would disappear — the session that
dies without anyone noticing (17.8) — and the two we do not have today would
appear: a key to keep safe and spending to cap.

Reaching GitHub would still have to be solved, and that is independent of
payment: either the executor sits on the development PC, or an IPv4 host is
needed. Not a problem to face now, but it is as well to know it is there and
that a subscription does not buy it.

### 17.2 — The cycle

```
 you → EMMA       "vorrei che ricordassi i miei appuntamenti"
        │
        ▼
 EMMA            recognises a development request and asks for confirmation
        │        (or you write it explicitly: "sviluppo: ...")
        ▼
 tasks table     the request sits there, in your own words
        │
        ▼
 me              I notice it, read the code, understand
        │
        ├──► CHECKPOINT 1   "this is how I read it, this is the plan. Go ahead?"
        │
        ▼
 me              implement, write the tests, verify
        │
        ├──► CHECKPOINT 3   "done, tests green, here is the diff. Commit?"
        │
        ▼
 me              local commit
        │
        ├──► CHECKPOINT 4   "committed <hash>. Push?"
        │
        ▼
 me              push to GitHub
        │
        ├──► CHECKPOINT 5   "pushed. Deploy to the VPS?"
        │
        ▼
 me              deploy, service restarted
        │
        ▼
 EMMA            restarts with the extra capability
```

The checkpoints are **1, 3, 4, 5**: the one between implementation and commit is
missing because there is no decision of yours there — if the tests fail I fix
them, I do not consult you. I show you the diff all the same, at checkpoint 3,
which is the moment you can still say "you misunderstood" at no cost.

Every checkpoint asks permission to **move to the next phase**, not to confirm
the one just finished.

### 17.3 — How the questions reach you without EMMA speaking first

This is the piece that makes "a single voice" and "no unsolicited message"
compatible. My questions end up in the same table; EMMA reports them to you
**when you ask her**, and your answer travels back the same way.

```
 you:   EMMA, a che punto sono i lavori?
 EMMA:  #3 — implementato, 53 test verdi. Committo?
        #4 — ho capito che vuoi X. Procedo?
        #5 — in attesa dalla fase 1.
 you:   sì al 3, il 4 no, intendevo altro
 EMMA:  Registrato.
```

The cost is latency: if you do not ask for six hours, I stand still for six
hours. But the work does not stop entirely — what comes before the first gate
carries on, and only publication stops, which is exactly what should stop.

Two mitigations, both compatible with the constraints:

- **batching**: a single "how far along are you?" resolves every open gate, as
  in the example above;
- **granting in advance for one job**: *"take #4 as far as the push"* leaves the
  strict default in place and gives you a fast lane when you already know what
  you want.

### 17.4 — Why it does not cost more

The point on which the idea would have broken. If I were the one checking the
queue every fifteen minutes, you would pay for every empty wake-up, and almost
all of them would be empty.

I do not have to be the one watching. A background shell command does the round
— `ssh` to the VPS, a `SELECT`, and if there is nothing it sleeps and tries
again — and **while it sleeps the model does not run**: what is working is
`bash`, which consumes no tokens. When it finds something the command exits, and
its exit wakes me.

Consumption happens only when there is real work: a day with no jobs costs zero
instead of ninety-six pointless wake-ups.

Since this is the subscription and not a metered key (17.1), the thing to
protect is not a bill but the **session's capacity**: every pointless wake-up
consumes context and usage, and a session that has to stay open for days cannot
afford it. The same mechanism that would avoid the spending avoids the
exhaustion.

On EMMA's side nothing changes: she stays on Groq's free tier, and filing a job
is a tool call, not a generation.

### 17.5 — Where the jobs live

**In the same SQLite file as the memory** (`data/emma.db`), in a `tasks` table,
managed by a separate module with a connection of its own — not inside
`SqliteConversationMemory`, which has a different responsibility.

The alternative was a second file, `data/tasks.db`. I ruled it out for a
concrete reason: everything built on 31 August — the integrity check at startup,
the `VACUUM INTO` snapshots, restoration from the healthy copy, the consistent
backup — applies **to that file**. A second database would either duplicate all
that machinery or be left uncovered by it, and it would be uncovered silently.
WAL mode makes concurrent access safe, so my reading over SSH does not disturb
the service writing to it.

The downside to accept: if the database were restored from a snapshot, the jobs
would go back too. That is consistent, and better than the alternative.

The minimal schema:

| Column | What it holds |
| --- | --- |
| `id` | sequential; it is the number you use when talking to EMMA about it |
| `created_at`, `updated_at` | when |
| `request` | the request **in your own words**, not summarised |
| `stage` | where we are: `nuovo`, `capito`, `committato`, `pushato`, `deployato` |
| `status` | `da_prendere`, `attende_te`, `in_corso`, `chiuso`, `abbandonato` |
| `note` | what I tell you at this checkpoint |
| `answer` | your answer, as EMMA recorded it |

### 17.6 — What EMMA needs (this is v0.3)

Three tools, all small:

- **`commissiona_sviluppo(description)`** — inserts a job. There are two ways to
  reach it: the explicit `sviluppo: ...` prefix, or recognition by the model
  followed by a confirmation from you. The second uses `gpt-oss-120b` for what
  it is good at — understanding an intention — and not to decide on its own: if
  it gets it wrong it costs you a "no".
- **`stato_lavori()`** — lists the jobs waiting for you, with my questions.
- **`rispondi(id, text)`** — records your answer.

They are precisely the first concrete tools for the agentic router, which has
been waiting for tools since v0.1: the tool-use loop is already there and
tested, the list is empty.

### 17.7 — The bootstrapping paradox

The tool that lets EMMA commission development has to be written the normal way,
by me with you at the PC. **The first link cannot generate itself.** From there
on the cycle closes and every later capability can arrive by that route.

### 17.8 — What can go wrong

- **The session dies** (a restart, a crash, exhausted context) and the waiting
  command dies with it: you carry on commissioning and nobody collects. It is
  the main risk, and it is structural — with no service (17.1), there is nothing
  that restarts by itself. A way of noticing is needed: a `last_seen` that I
  update on every wake-up and that EMMA reports when you ask for the status, so
  that *"last contact: two days ago"* tells you the session needs reopening.

  **Observed on 31 August, and worse than expected.** The watcher did two
  regular rounds (13:29, 13:34, exactly 300 seconds apart) and then stopped
  before the third, **with no exit code and no errors**: it did not die of a
  defect of its own, it was dismantled from outside. A background command is not
  guaranteed to outlive the session that started it for long.

  From which follows an honest division of roles, worth keeping in mind rather
  than discovering again:

  | Piece | Reliability |
  | --- | --- |
  | the `SessionStart` hook (17.6bis) | **certain** — fires on every opening, no process to keep alive |
  | the `watch-tasks.sh` watcher | **best effort** — useful while it lives, not a guarantee |

  The practical result is acceptable all the same: you open a session and know
  at once whether there is work; while you work, if the watcher is alive it
  wakes you. What **cannot** be promised is "I commission it at night and find
  it done".
- **The context runs out** on a long session: I remember the decisions, not
  every detail. `SESSIONS.md` and `ROADMAP.md` are the real memory and have to
  be updated often, not at the end of the session.
- **The weak model misreads the intent**: contained by the confirmation.
- **The job is ambiguous**: checkpoint 1 exists for this. I ask, I do not guess.
- **The SSH key on the PC** gives access to the VPS. That is already the case
  today, but in this scenario the session uses it unattended: it is worth it
  being a dedicated key with a restricted `command=`, not the administration
  one.

### 17.10 — State is not asked of a tool: it is put in front

**Discovered in production on 31 August**, and it is the most general lesson in
this whole entry.

The user asked EMMA which jobs were outstanding. She reported one of the two,
and described it with the very interpretation he had explicitly rejected. In the
logs: `tools=0`. **She had not called the tool at all** — she had repeated, word
for word, a wrong answer given fifteen minutes earlier and stored in the
persistent memory.

Here is the interaction we had not foreseen: **the memory (v0.2) and the tools
(v0.3) damage each other.** An answer derived from a tool, once stored, becomes
indistinguishable from a fact; and at the next question the model reuses it
instead of asking again. It is not specific to jobs: it holds for any tool that
reports state that changes.

**Measured, ten attempts per configuration, the same question:**

| Configuration | Correct answers |
| --- | --- |
| poisoned history, no context | 6/10 |
| poisoned history + context | 8/10 |
| clean history, no context | 9/10 |

**Why instructing the model is not enough.** An instruction in the prompt is a
request for cooperation: it moves those numbers, and it moves them again — in a
different way — on the next model. Recalibrating the prompt at every change of
provider is the opposite of the discipline that holds this project up, where the
router speaks one language and it is the adapters that bend.

**The solution: `ContextProvider` in `core/router.py`.** A protocol with a
single asynchronous method returning the current state in one line. The router
asks it once per turn — not on every tool round, because the state does not
change mid-turn — and appends the result to the system prompt.

The properties that matter:

- **There is no decision to get wrong.** The line is there regardless; a stale
  memory is contradicted by something already on the page, instead of by a
  lookup nobody performed.
- **`core/` still does not know what a job is.** `main.py` hands it the
  provider, as it does the tools: the same discipline as v0.1.
- **Provider-independent.** It is text in the prompt, not a `tool_choice` to be
  translated between two dialects. Changing model does not silently degrade the
  behaviour.
- **A provider that fails does not cost the answer.** It is logged and skipped.

**Alternatives rejected.** Forcing `tool_choice` would have required
recognising "this is a status question" without using a model — keyword
matching, fragile and language-bound. Not storing tool-derived answers in memory
removes the poison but also the continuity: EMMA would forget what she had just
said.

**The honest limit.** Even this does not reach a guarantee: it remains a
decision of the model, and the numbers remain the numbers of *this* model. What
the provider gives is not a better rate, it is that the updated truth is always
in view — so the behaviour does not degrade in secret the day the model changes.
It is structural stability, not statistical.

**Verdict: done** (31 August 2026), together with cleaning up the poisoned
history, which on its own was worth three answers in ten.

### 17.9 — What stays out, deliberately

- **EMMA does not know the code.** She is the front desk, I am the workshop.
  Feeding her the repository on every turn would cost tokens for a judgement I
  would redo anyway, having the history, the tests and the roadmap in front of
  me.
- **No automatic deployment.** That is checkpoint 5, always.
- **EMMA does not propose improvements on her own initiative.** She records
  yours.
- **No second bot.**

**Verdict: to do as v0.3**, in the order 17.6 → 17.5 → 17.4. The value is not
the automation — twenty minutes of work stay twenty minutes — but the fact that
an idea had away from the keyboard is not lost, and that your judgement comes in
four times instead of never.

---

## 18. Persistent fact memory (proposal, 31 August 2026)

**Where it comes from.** A question of yours: *"se le chiedo di ricordarsi che
a=2, dopo 20 prompt se lo scorda?"* — if I ask her to remember that a=2, does
she forget it after 20 prompts? Yes, and worse than it looks — verified by
running the code, not by deducing it.

### 18.1 — What actually happens today

`MAX_HISTORY_MESSAGES` counts **messages, not exchanges**: every exchange
consumes two, so 20 is about ten exchanges. A fact stated at the beginning
survives to the ninth and disappears at the tenth.

And it does not "forget": `SqliteConversationMemory._prune_locked` runs a
`DELETE`. The database does not keep everything and show twenty — **it keeps
twenty in total**. That text is no longer recoverable by anybody, not even by
reading the file.

The survival criterion is **age**, not importance: `a=2` dies alongside "what
time is it".

### 18.2 — Where the tokens go, measured

Before proposing anything it is worth knowing what costs what. An estimate at ~4
characters per token, validated against the real logs (1,927 estimated,
1,900–2,600 observed):

| Component | Tokens | Share |
| --- | --- | --- |
| system prompt | ~755 | 39% |
| tool declarations | ~537 | 28% |
| history (20 messages) | ~600 | 31% |
| the context line | ~35 | 2% |

**The fixed cost is more than double the history.** From which follows a
correction to advice I had given verbally: reducing `MAX_HISTORY_MESSAGES` from
20 to 10 saves ~300 tokens out of 1,900, that is **15%**, not "nearly half". You
would lose half the memory for a sixth of the consumption: **not worth it, and
this entry exists partly so as not to repeat that mistake.**

The real levers on consumption are the system prompt and the tool descriptions —
paid for even when the user only writes "hello". But they are also what makes
the model decide well, so shortening them is a trade-off, not a net gain.

### 18.3 — The routes, and why one remains

| Route | For | Against |
| --- | --- | --- |
| a larger window | one line in the `.env` | linear cost, modest benefit (18.2) |
| **persistent facts** | they do not expire, they cost little, provider-independent | somebody has to decide what a fact is |
| automatic summarising | preserves the sense at a reduced cost | unpredictable loss, and **a wrong summary is worse than none** (entry 17.10) |
| search over old messages | unlimited history | needs infrastructure out of scale for this project |

I would rule out summarising because of the lesson in entry 17.10: the plausible
wrong answer is the one nobody thinks to check.

### 18.4 — The shape: a module, not a piece of the core

The two attachment points already exist and were built today: the `Tool`
protocol and the `ContextProvider` protocol. A memory module would be
`tools/memory/` with its own tools, its own context provider and its own table,
registered with **one line in `main.py`** — and removed by removing that line.

`core/` would carry on not knowing what a memory is, just as today it does not
know what a job is.

### 18.5 — The relationship with Anthropic's memory tool

It exists and it is real: you declare `{"type": "memory_20250818", "name":
"memory"}`, the model receives file operations (`view`, `create`,
`str_replace`, `insert`, `delete`, `rename`) and you implement the backend — the
Python SDK offers `BetaAbstractMemoryTool` as a base.

**But it is a tool defined by Anthropic**, so it does not work on Groq. Adopting
it would tie the memory to one provider, which is exactly the constraint the
user set in entry 17.10. What can be taken is **the pattern, not the API**: one
tool to write, one to read, and injection into the context.

The sophistication would be lost — there the model organises a file tree by
itself, here there would be a flat list. For a personal assistant the flat list
is probably enough, and one can always go deeper later.

Worth noting: Anthropic's memory tool **has the same weak point** measured in
entry 17.10, because it remains a tool the model has to *choose* to use. The
defence is the same: the context provider.

### 18.6 — The real problem, which no route solves

**Who decides what is a fact worth remembering.**

- The model decides → it gets it wrong, and this is not a hypothesis: 6 times
  out of 10 it did not even call the tool in front of it (17.10).
- The user decides with an explicit prefix (*"ricorda: a=2"*) → reliable, less
  magical. It is the same choice made for `sviluppo:` in 17.6, and it held there.

And the problem neither of them addresses: **facts contradict each other and
age.** `a=2` today, `a=3` in a month, and now there are two. A declared policy is
needed — last one wins? does it ask you? — and pruning, or they grow until they
cost as much as the window they were meant to replace.

**Verdict: to do, but not before the project is stable.** It is the most useful
tool on the list — an assistant that forgets everything after ten exchanges
stays a chat — but it has to be designed with the conflict policy decided
*first*, not discovered afterwards.

---

## 18-bis. Fact memory: implemented on 1 September 2026

Entry 18 was a proposal; this is what was built, and the two things in which
measurement corrected the design.

`tools/facts/` — called **facts** and not **memory** as written in 18.4, because
`core/memory.py` already exists and is the opposite: it forgets by age. Two
modules called "memory" saying opposite things about forgetting are a name
collision this project has already paid for once.

**Two tools, not three.** No `recall`: everything is in the context already, and
a third declaration would be paid for on every turn in order to answer a
question whose answer the model can already see.

**The two corrections that came from measurement, not from reasoning:**

1. **I had underestimated the cost to the user.** I had told him +15%. The tool
   declarations alone cost **303 tokens on every turn**, paid even when unused:
   the cost starts at +13% with zero facts. The estimate had counted the facts
   and forgotten the tools for managing them.
2. **The cap promised more than it could keep.** `MAX_ACTIVE_FACTS` was 100, but
   the 4,000-character limit on the injected context lets ~80 in: the rest would
   have been stored, counted and never seen by the model. Brought down to **50**,
   the two limits stop competing for the same role — the count constrains normal
   use, the character limit stays as a defence against long facts.

**Final cost, measured on production traffic:** from ~84 exchanges/day to ~75
(no facts), ~64 (thirty facts), ~59 (at the cap).

**Verified before wiring anything up:** the 357 pre-existing tests ran
untouched, all green. After wiring, exactly one failed — the one asserting the
exact set of tools, that is, the test doing its job. Verified in particular that
the three stores coexist on the same SQLite file without the window ceasing to
prune, the facts starting to expire, or the integrity check failing.

## 19. Nobody queries `/health` (proposal, 31 August 2026)

During the review for production I made the `/health` endpoint honest: before,
it answered `"status": "ok"` under all circumstances, a dead database included.
Now it really reads from the store before answering and returns `503` with
`"status": "degraded"` when it cannot, together with the turn count and the
reason for the last degradation.

**But the real problem remains: nobody reads it.** I searched `systemd/` and
`scripts/` and there is not a single consumer. A monitoring endpoint nobody
queries has never prevented a fault — and this evening you noticed the faults
three times before the service did.

I did not wire it up on my own because touching `systemd/` or adding a periodic
job means changing the deployment layout, which rule 2 forbids me to do without
your asking. The options, from the lightest:

| | How | Cost | What you get |
| --- | --- | --- | --- |
| A | `ExecStartPost` / a timer running `curl -f localhost:8000/health` | one line of unit | systemd knows it is degraded, and writes it in the journal |
| B | `WatchdogSec=` + `sd_notify` from the process | one more dependency (`systemd-python`) and code in the lifespan | systemd **restarts** EMMA when she stops being well |
| C | Adding the check to `scripts/backup.sh`, which already runs at 03:30 | a few lines of shell, no new units | you find out within 24 hours, and the backup knows whether it is saving a healthy DB |

**C was implemented on 31 August 2026** (`scripts/backup.sh`): it queries
`/health` before writing the manifest, records the outcome in the journal and in
`MANIFEST.txt`, and never makes the backup fail — a stopped service is a reason
to keep the data, not to skip it. Tested against a real server in all three
cases (200, 503, no answer); the third revealed a defect in the code just
written, because `curl` already prints `000` by itself and the fallback added a
second one.

**My view was:** **C** is the one worth most immediately and costs least of all
— the nightly job already exists, runs regardless, and has a reason of its own
for wanting to know whether the database is well *before* copying it. **B** is
the right long-term solution but is the only one that adds a dependency, and an
automatic restart on a service that talks to you through Telegram has to be
decided by you, not by me: a restart loop is worse than a degraded service that
answers.

**Verdict: C is worth it, but it is a change to the deployment — your call.**

## 20. Splitting `core/llm.py` (evaluated and rejected, 31 August 2026)

The review plan said "split `core/llm.py`", which was at 758 lines against the
480 of the next module. I did something else instead, and here is why.

**What I did.** The duplication, not the size, was the real defect. The two
clients had two structurally identical `except` ladders, and that is exactly the
drift that had already produced a real bug: for a whole release the Groq client
ignored every tool declaration, because the function had been added to one copy
and not the other. Looking at the two files separately, that bug was invisible.
The two SDKs have an **identical** taxonomy — `APIConnectionError`,
`RateLimitError`, `APIStatusError`, and a root that differs only in name — so
the ladder is now written once (`_RetryLadder`) and parameterised. The two
`complete()` methods went from 107 and 81 lines to fewer than 40 each, and not
one provider-specific `except` clause remains. The log formats stayed identical,
verified line by line.

**What I did not do, and why.** Splitting the file remained. The obvious
candidate was the three functions translating the Groq dialect: 149 lines, pure,
with a dedicated test file (`tests/test_llm_groq_tools.py`) — that is, all the
signs of a concern already separate in fact.

It is not. Those functions depend on the vocabulary (`Message`, `TextBlock`,
`ToolUseBlock`, `LLMResponse`, `_text_of`) that lives in `core/llm.py`, and
`core/llm.py` would have to import them: **a circular import**. The seam is not
where it appeared to be. To exist it would need a third module:

| Module | Contents | Estimated lines |
| --- | --- | --- |
| `core/messages.py` | `Message`, the block types, `LLMResponse`, `_text_of` | ~90 |
| `core/groq_dialect.py` | the three translation functions | ~160 |
| `core/llm.py` | the protocol, the errors, `_RetryLadder`, the two clients | ~530 |

It is cleaner. But it is a change of module boundaries — rule 2 — decided on the
eve of a release, for a gain that at that point is only the size of the file:
the duplication was already gone, no function exceeds 40 lines except the pure
translations, coverage is at 94%. A reader chasing `LLMResponse` would open
three files instead of one.

### Revisited on 1 September 2026, on a direct question: would you split it?

No. And in measuring it I changed my mind about **which** the problem is.

The file looks twice as big as it is. Counting the same way across every module
— total lines minus docstrings, comments and blank lines:

| Module | Real code | File |
| --- | --- | --- |
| `core/llm.py` | **407** | 798 |
| `core/memory.py` | 232 | 472 |
| `core/router.py` | 204 | 488 |
| `core/tasks.py` | 172 | 348 |
| `adapters/telegram.py` | 144 | 303 |

34% of `core/llm.py` is docstrings, which in this project are deliberate. 407
lines of code in a module are not a defect, and splitting on the basis of the
798 would mean reacting to a number that mostly measures prose. On top of that
**three** files would be needed rather than two, because of the circular import
described above: three files for 407 lines make navigation worse, not better.

**The real problem was something else, and the line count was hiding it.**
`_RetryLadder` called `_check_rate_limit` around line 280, and that function was
defined at 537: reading from the top you met the call **three hundred lines
before its definition**. That is what makes a file feel longer than it is — and
splitting it does not solve that, it moves it into another file.

Fixed on 1 September 2026: the three functions that classify a fault moved above
the ladder that uses them, and the module has six section separators. It now
reads from top to bottom without jumps: errors, what a response is made of, how
a fault is judged, the ladder, the two clients, the dialect translation. No
other module in the project has separators, because no other is long enough to
need them.

**When to split it for real: at the third provider.** Today there are two
dialects (native Anthropic, and Groq speaking OpenAI). A third would make
translation the dominant concern of the file, and at that point the seam is
worth the third module it costs. That is a verifiable criterion, not a "later
on".

**Verdict: deduplication and reordering are worth it and are done. Splitting
into three modules is not worth it now — ask the question again when a third
provider arrives.**

## 21. One turn at a time, and nobody writes it down (proposal, 1 September 2026)

`Router.handle()` reads the history, then spends **seconds** inside the model,
then writes the two rows. Between the read and the write it holds no lock. Two
simultaneous turns on the same conversation would interleave their writes: at
best the order of the stored messages is not the real one, at worst the sliding
window cuts away the question and leaves the answer.

**Today it does not happen**, and I verified that rather than hoping it: the
adapter builds the PTB application with the default values, and in
python-telegram-bot 22.8 `max_concurrent_updates` is **1**. Updates are
serialised, so there is at most one turn at a time. The two stores
(`SqliteConversationMemory` and `TaskStore`) each have their own `asyncio.Lock`
and `append` holds the lock across insert+prune, so the single operation is
already atomic: only the atomicity of the *turn* is missing.

The problem is that this correctness depends on a library's default value, which
no file declared. I added the comment in `core/router.py`; this entry is the
follow-up.

**What would break it**, in order of likelihood:

| | Change | Effect |
| --- | --- | --- |
| 1 | The **voice satellite on the Raspberry Pi** (already on the roadmap) | a second channel, a second turn in parallel: it breaks |
| 2 | `concurrent_updates=True` to let the bot answer while it works | it breaks |
| 3 | A second whitelisted user | different conversations, so different rows: it does not break |

**The fix**, when it is needed: a `dict[str, asyncio.Lock]` per conversation in
the router, taken around the whole turn. Not around the writes alone — that
would be useless, because the problem is that the history read at the start is
already stale by the time of the write. Cost: ten lines or so, and the
per-conversation serialisation that already exists in fact becomes declared.

**Verdict: do not do it now** — it would be code protecting against a condition
that cannot occur, and untestable without manufacturing the concurrency that is
absent. **Do it as the first step of the voice satellite**, before adding the
second channel, not after.

## 22. Deployment never removes anything (proposal, 1 September 2026)

Discovered while looking at why the check in entry 19-bis was reporting a file
on a deployment just made. The immediate cause was mine and is fixed; this is
the other thing that came to light along the way.

The remote step of `scripts/deploy.sh` does:

    tar -xzf /tmp/emma-deploy.tar.gz -C /opt/emma

`tar` **overwrites, it does not synchronise**. A file deleted from the
repository is never removed from the server: it stays there forever. The
consequences, in order of severity:

1. **A deleted Python module stays importable.** If tomorrow
   `tools/introspection.py` is removed and something still imports it by
   mistake, in development the import fails at once and in production it
   **works**, executing code that no longer exists in any commit. It is the kind
   of divergence that makes a bug irreproducible.
2. Leftovers nobody ever knowingly shipped. `/opt/emma/.pytest_cache` is there
   today (56K) and so is `.cache`, both already on the archive's exclusion list:
   they arrived before that list existed and never left.
3. A renamed file exists in production under both names.

**The options**, from the lightest:

| | How | Risk |
| --- | --- | --- |
| A | Before extracting, delete only the directories that are shipped in their entirety (`core`, `adapters`, `tools`, `tests`, `scripts`, `docs`, `prompts`, `systemd`) | low, but if the extraction fails immediately afterwards the installation stays broken |
| B | Extract into `/opt/emma.new`, then swap the directories with `mv` | the swap step is not atomic for `.env`, `data/` and `.venv`, which have to be grafted back |
| C | `rsync --delete` with the exclusions, instead of `tar` | the cleanest and the most correct; requires `rsync` on the server and rewrites half the script |

**My view: C.** It is the only one in which "what must be on the server" is
written in a single place instead of being the sum of every past deployment. A
is a sticking plaster that moves the risk to the worst possible moment. B is
complicated exactly where it must not be.

I did not do it because it is a rewrite of the road every release travels, and
changing it at one in the morning straight after a successful deployment is not
a good idea. To be done with a fresh head, with a trial deployment to a dummy
directory before pointing it at `/opt/emma`.

**Verdict: C is worth it, but it has to be done awake and rehearsed first.**

## 23. Noticing a job commissioned while the session is open (1 September 2026)

The user put a job in the queue while the session was open, and I did not
notice. It was not inattention: **no mechanism existed that could have told
me.**

There was a single hook, `SessionStart`, which runs `scripts/queue-brief.sh`
when the session opens and never again. A session lasting hours has no way of
knowing that the queue has changed in the meantime. Jobs #5 and #6 from
yesterday evening I discovered by accident, because I was abandoning #4 and ran
`ssh emma-queue list` for another reason.

**The three cases, and what each is covered by:**

| When the job arrives | Before | Now |
| --- | --- | --- |
| Before the session opens | `SessionStart` | the same |
| With the session open, and then the user writes | **nothing** | `UserPromptSubmit` |
| With the session open, and the user does not write | **nothing** | a background watcher, on request |

**What I did.** `queue-brief.sh` now takes the event name as an argument (Claude
Code discards output whose `hookEventName` does not match the hook that ran it)
and the connection timeout as a second. The two callers want different values:
at startup ten seconds spent finding out are free, on every message they are ten
seconds of the user waiting, so that caller passes four. Measured: 600–700 ms
warm, 1.5 s cold, 1.4 s when the server is unreachable — and in that case it
exits 0 without printing anything, so the message goes out regardless.

The event name ends up inside hand-built JSON, so it is validated: a quote there
would produce output Claude Code cannot read, and it would fail **silently** —
the worst way a notification can fail.

**The third row stays uncovered by default, and it is honest to say so.** If the
job arrives and the user writes nothing, no hook fires: hooks are reactions to
session events, and "nothing happens" is not an event.
`scripts/watch-tasks.sh` exists for this — it queries the queue and exits as
soon as there is work — but it has to be started explicitly in the background by
the session, dies with it, and yesterday evening stopped by itself after two
cycles. It is best-effort by construction (entry 17.8: there is no service
behind it).

### Completed on 1 September 2026: the third case too

At the user's request ("set up automatic restarting of the watcher, and make it
persistent"). Claude Code has exactly the right mechanism: an `asyncRewake` hook
runs in the background and wakes the model when the command **exits with 2**. No
service was needed: what was needed was making `watch-tasks.sh` fit to be that
command. Three obstacles, two of them real traps.

**Exit code 2 meant the opposite.** In normal mode it means "I gave up after six
hours". Wired this way, it would have woken the session precisely when there was
nothing to say. Hook mode inverts them, and documents it.

**It would have spiralled.** The `Stop` hook re-arms the watcher on every turn;
with the same job still queued it would have woken, restarted, woken again —
forever, if that job is waiting for an answer. It now remembers the ids it has
announced, and a lockfile (with the pid verified, not believed) makes re-arming
idempotent.

**The third was mine:** a `break` inside a `case`, which is not a loop, so it
exited the `while` and the watcher died after five seconds in silence. Found by
a test asking "is it still alive?", not by rereading.

**The local cache, and a design error I corrected myself.** I had proposed that
the hook should *read* a cache instead of querying the server: instantaneous. It
is wrong — a cache five minutes old may not contain the job just filed, which is
the very defect all of this exists to close. The right order is the reverse:
**the server first, the cache only if it does not answer**, declaring how old
the figure is. A scheduled task every 5 minutes keeps it warm even with no
session open.

**The scheduled task lasted an hour.** The user saw a terminal window flash
every five minutes: `-Hidden` in `New-ScheduledTaskSettingsSet` hides the task
in the list, not the window, and with `LogonType: Interactive` it runs inside
the user's session. I could not have noticed — I do not see the screen — and it
is the one class of defect where the user is the only measuring instrument
available.

Disabled, not deleted, at his request. And the evaluation has to be redone with
the real cost on the table: the cache is already rewritten on every message and
every session opening, so the task added only refreshes while no session is open
— that is, when there is nobody to tell. A permanent annoyance for a marginal
gain is a bad trade, and I had proposed it, calling it "the smaller half",
without knowing it also had a visible cost.

There too, a defect found by trying it: under `set -o pipefail`, a `grep` that
finds nothing exits 1, so an **empty queue** was indistinguishable from an
unreachable server — and with the queue just emptied the script announced "5
jobs" read from the cache. Exactly the opposite of its purpose.

**Verdict: all three cases done.** The third stays tied to the session — it dies
with it, and between the wake-up and the re-arm there is a window of a few
seconds. Making it guaranteed would mean a service that outlives the session:
precisely the infrastructure this project has chosen not to have.

## 24. Removing a tool in two stages (the user's proposal, 2 September 2026)

**The idea is his, and it is better than mine.** I had proposed a variable in
`.env` with the list of disabled tools, read at startup. He proposed two stages:
on the first request the tool is **only disabled**; on the second, and **only if
it is already in the disabled state**, removal from the code goes ahead.

**Why it is better.** The second stage already exists: "take a tool out of the
codebase" is an ordinary job in the development queue. So it is not a new
mechanism, it is **a gatekeeper in front of one that is already there**. And it
is the right gatekeeper: "only if already disabled" means one has lived without
that tool for a while, so final removal is never a decision taken in the heat of
the moment. It is the same philosophy as an `abandon` that does not delete and a
corrupt database put in quarantine.

**Where the complexity lies, and it is not where it appears.** The router builds
the declarations with `_tool_schemas()` **on every turn** (`core/router.py`,
line 362), not once at construction. That is a piece of luck: filtering out
disabled tools costs a few lines there, without making the set the router
receives mutable and without a restart. The effect would be immediate.

**The three design questions:**

| | Question | Proposed answer |
| --- | --- | --- |
| 1 | Where does the "off" state live? | In the database, next to the facts: it survives a restart, it goes into the backup, it is inspectable |
| 2 | Who can switch things off? | If it is a tool EMMA calls, it can switch off the one needed to switch things back on. `list_tools` and the re-enabling tool must not be switchable |
| 3 | What does the model see? | A disabled tool disappears from the declarations, so it cannot be called; `list_tools` should still be able to show them as "disabled", or the user does not know what to switch back on |

**The cost:** one more tool (off/on) paid for on every turn, plus the filter.
Estimable at around 150–200 tokens/turn, that is ~4 exchanges/day.

### Implemented on 2 September 2026

Done at the user's request. The three questions got the proposed answers, and a
fourth emerged while building it.

| | Question | How it ended up |
| --- | --- | --- |
| 1 | Where does the state live? | A `tool_state` table in the same SQLite file: it survives a restart, it goes into the backup, it is inspectable |
| 2 | Who can switch things off? | `PROTECTED` = `list_tools` + `enable_tool`. A test verifies that those names really exist: a guard on a misspelled name protects nothing |
| 3 | What does the model see? | A disabled one disappears from the declarations; `list_tools` lists it as *(disattivato)* |
| 4 | **And if it calls it anyway?** | Refused at execution too. A call may already be in flight from a turn in which the tool was still offered: hiding the declaration is what *usually* suffices, refusing is what makes it a guarantee |

**`ToolGate` is a protocol in `core/router.py`**, like `Tool` and
`ContextProvider`. The router imports nothing from `tools/`: it asks whatever it
was handed. And a gate that does not answer does not cost the turn — everything
is offered, because of the two wrong directions that is the less serious:
offering a tool that should have kept quiet costs one capability set aside,
hiding them all would leave the assistant unable to do anything and unable to
explain why.

**The cost is the highest so far:** +276 tokens per turn for the two
declarations, from ~65 to ~59 exchanges a day. It is worth saying because it is
the kind of addition that is paid for forever and used rarely — if one day the
quota tightened, these two are among the first candidates to be switched off by
themselves.

### Reviewed on 3 September 2026, and corrected in three places

A review requested by the user and given to a separate reviewer, with context
built for the purpose instead of the session's history. It found three true
things, and the first is the most instructive.

**1. "Already off" was a counter, not evidence.** `disabled_at` was written and
never read. A turn allows up to five tool rounds, so the model could switch a
tool off and ask for its removal **in the same breath**. The code required two
calls; the documents — this one included — promised two occasions.
`MIN_TIME_OFF_SECONDS` (one hour) was added, and now the sentence is true. Worth
noting: it is a small deviation from the user's original design, which said
"only if already disabled" and said nothing about time. Time is what makes the
*reasoning* he gave true.

**2. The gate was stale for the rest of the turn, and the comment saying
otherwise was one I had written myself.** It said *"neither of the two can
change under the assistant mid-turn"* — true before this feature, false
**because of** this feature: switching a tool off is itself a tool. The visible
consequence: EMMA said *"from now on I will not use it"* and used it on the next
round. The declarations are now recomputed on every round.

Writing the test turned up a narrower hole still: the model can emit
`remove_tool(x)` and `x` **in the same round**, and the set read at the start of
the round would already be stale. So the refusal at execution now consults the
gate at the moment of the call instead of receiving it. Cost: one indexed query
per tool call.

**3. The router did not have a single test.** The store and the tools were
covered thoroughly; the router carries two of the four requirements and was the
untested part. I had demonstrated it by hand in a throwaway script — that is,
exactly the kind of evidence that disappears. Now there is
`tests/test_router_gate.py`, and `core/router.py` is at **100%**.

**Also fixed:** `PROTECTED` enforced in the store as well (a guard in one place
only has a service door next to it); no duplicate jobs on a third request; a row
that outlives the tool it names is no longer counted by `list_tools` (it said
"of which 1 disabled" while marking none); the shared mixin refuses instead of
skipping validation when it has not been wired; `EnableTool` no longer inherits
a mixin it does not use.

**One clarification where the review pressed too hard:** the second stage does
not delete code, it files a job in a queue that a human reads and that
`abandon_development` can remove. Less irreversible than it was described — but
the substance held, because the documents promised evidence the code did not
provide.

**Verdict: done and reviewed. 490 tests, `core/router.py` at 100%.**

**A related note:** the user also observed that one day *"a personalisation
tool"* will be needed — the user's name was taken out of the prompt because
`prompts/system_prompt.txt` is tracked and public (rule 7), but a personal
assistant that does not know what you are called is an oddity. The natural shape
is the same as the facts: personal data in the database, not in a versioned
file.

## Summary of verdicts

| # | Entry | Verdict |
|---|------|----------|
| 1 | The adapter pattern | not worth it (a richer response: when it is needed) |
| 2 | A hand-written agentic router | not worth changing |
| 3 | Single-user whitelist | future phase, with the tools |
| 4 | Hand-written `.env` config | future phase (pydantic-settings); `LOG_LEVEL` first |
| 5 | Indiscriminate retry | **to weigh now**: distinguish final errors |
| 6 | In-memory memory | v0.2 phase: SQLite + WAL, truncation and summaries |
| 7 | Readable logs on stdout | not worth it (JSON only with the tools) |
| 8 | Languages | done differently: everything in English from 3 September 2026, the personality and the tool strings excepted |
| 9 | Telegram long polling | not worth changing |
| 10 | Flat project structure | future phase (`src/`) only if it becomes a package |
| 11 | `Restart=always` | future phase (an `sd_notify` watchdog) |
| 12 | FastAPI + uvicorn | not worth changing, but it is the first possible simplification |
| 13 | `tar.gz` backups | future phase: priority to the copy away from the house, then restic |
| 14 | A single `CLAUDE.md` | future phase: a short core + `.claude/skills/` |
| 16.1 | `tar` of a live DB | **done** (31/08/2026): `VACUUM INTO`, verified, in `backup.sh` |
| 16.2 | WAL + `integrity_check` at startup | **done** (31/08/2026), with restoration from a snapshot |
| 16.3 | A generic automatic mirror | not implemented: you restore on a diagnosis, not on a symptom (16.5) |
| 16.4 | Periodic snapshots | future phase, if the loss window turned out to be too wide |
| 17 | EMMA as the commissioner of her own development | **to do as v0.3**: three tools, a queue in the database, checkpoints 1/3/4/5 |
| 18 | Persistent fact memory | future phase |
| 24 | Removing a tool in two stages | **done and reviewed**; the review found that "already off" was a counter and not evidence |
| 23 | Noticing a job while the session is open | **all three cases done**; the scheduled task was disabled: a flashing window for a marginal gain |
| 22 | Deployment overwrites and does not synchronise | **C** (`rsync --delete`): a deleted module stays importable in production |
| 21 | A per-conversation lock in the router | not now; **the first step of the voice satellite**, before the second channel |
| 20 | Splitting `core/llm.py` into three modules | **no**: 407 lines of code out of 798, the rest is documentation. Deduplication and reordering done; reassess at the third provider |
| 19 | Wiring something to `/health` | **C implemented**: a check inside `backup.sh`, on 31 August 2026 |

Entry 5 was implemented in v0.1.x (retrying only transient errors). 16.1 and
16.2 were implemented on 31 August 2026. Everything else is material for the
phases already on your roadmap.
