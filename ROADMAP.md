# ROADMAP

Checklist of planned work by version. Check off items as they land in a commit.
Propose new items in `REVISIONE.md` first; move them here once approved.

---

## v0.1.x — Maintenance (current)

- [x] Fix: permanent 4xx errors no longer retried (`core/llm.py`)
- [x] Fix: blank lines preserved across Telegram message splits
- [x] Tests: `test_llm.py` — retry / no-retry coverage
- [x] Tests: `test_telegram.py` — `_split_message` coverage
- [x] Model: default upgraded to `claude-sonnet-4-6`
- [x] Multi-provider LLM: `LLM_PROVIDER` selects Anthropic or Groq at boot
- [x] `GroqLanguageModel` in `core/llm.py` — OpenAI-compatible, same retry policy
- [x] Config: `llm_provider`, `groq_api_key`, `groq_model` fields added
- [x] Docs: `GUIDA.md` updated (section 2.8, Appendice B) for new variables
- [x] Privacy: real IPs, hostnames and personal names removed from all tracked files
- [x] CLAUDE.md rule 7: mandatory privacy check before every push
- [x] Deployment: tested on local Ubuntu VM and production VPS (IPv6-only)
- [x] Commit + backup for Session 3 changes (commit `c8a8c5a`, backup `emma-20260830-170129.zip`)
- [x] Regenerate `docs/GUIDA.pdf` from updated `docs/GUIDA.md`

---

## v0.2.0 — Persistent memory (SQLite) ✓ complete

Goal: conversation history survives a restart. Zero change to the router or
the adapter — only a new `ConversationMemory` implementation swapped in.

- [x] Design SQLite schema (one table: `messages(conv_id, role, content, ts)`)
- [x] Add `MEMORY_DB_PATH` to `config.py` and `.env.example`
- [x] Implement `SqliteConversationMemory` in `core/memory.py` via `aiosqlite`
- [x] Wire it in `main.py` (open/close in lifespan, replaces InMemory)
- [x] Tests: `test_memory_sqlite.py` — 9 tests including persistence across reopen
- [x] Update `docs/GUIDA.md` (new variable, first-run DB creation)
- [x] Update `CHANGELOG.md` and this ROADMAP
- [x] Backup + commit + deploy (verificato in produzione su Aruba)

---

## v0.2.1 — Database integrity ✓ complete

Goal: the conversation history is backed up correctly and repairs itself when
the file is damaged. Proposed and reasoned through in `REVISIONE.md`, entry 16.

- [x] Fix: `backup.sh` archived a live SQLite file (`VACUUM INTO` + verification)
- [x] `data/` excluded from the tar; `MANIFEST.txt` reports the database status
- [x] `PRAGMA journal_mode=WAL` in `SqliteConversationMemory.open()`
- [x] `PRAGMA integrity_check` on open, with quarantine of the damaged file
- [x] Recovery from the newest healthy snapshot, falling back one generation
- [x] Snapshots via `VACUUM INTO` on open and clean shutdown, two generations
- [x] Tests: 8 further tests in `test_memory_sqlite.py` (51 total)
- [x] Docs: `GUIDA.md` ch. 1, 3.3, 5.6, 6.5, 6.6, 6.7; `CHANGELOG`; `REVISIONE` 16.5

Found during the pre-publication review, all install-blocking:

- [x] Fix: `emma.service` had `ProtectSystem=strict` and no `ReadWritePaths`,
      so 0.2.0 could not create `data/` on a clean install
- [x] Fix: `emma-backup.service` could not read a WAL database (needs write
      access to the `-shm` file beside it)
- [x] Fix: `backup.sh` mis-resolved an absolute `MEMORY_DB_PATH`
- [x] Clear error naming `ReadWritePaths` instead of a bare `OSError`
- [x] Guide: `data/` created in ch. 4.6 before the unit starts; upgrade note
- [x] Verified: cold start, both path shapes, backup end-to-end, 51 tests

Found during the production deploy:

- [x] Fix: snapshots came out `0644` (VACUUM INTO uses the process umask)
- [x] Fix: pip's HTTP cache was archived nightly (23 MB → 340 KB per archive)
- [x] Deploy on the production VPS and verify (hardened units now in place,
      snapshot written at start-up, history intact, backup verified end-to-end)
- [x] Backups always happen: second disk when there is one, `/var/backups/emma`
      otherwise, never nothing. Timer enabled and verified on the VPS.

---

## v0.3.0 — Real tools

Goal: EMMA can look things up and take actions. Each tool is a Python class
implementing the `Tool` protocol in `core/router.py`.

The first tool set is the one that lets her ask for the others: EMMA
commissions her own development, a developer picks the request up, and she
comes back with the capability. Designed in `REVISIONE.md` entry 17.

**Step A — EMMA's side**

- [x] Decide and document the first tool set (`REVISIONE.md` entry 17)
- [x] `core/tasks.py`: the queue, six stages, four checkpoints, heartbeat
- [x] `tools/development.py`: `request_development`, `work_status`,
      `answer_question`
- [x] Register them in `main.py`; router untouched, as the protocol promised
- [x] `prompts/system_prompt.txt`: the new tools, and the stale claim about
      forgetting past conversations
- [x] Tests: 39 new, 91 total, ruff clean
- [x] Update docs (GUIDA 2.4, 3.3bis, 5.5, 5.6), CHANGELOG, ROADMAP
- [ ] Backup + commit
- [ ] Deploy and verify end to end from Telegram

**Step B — the workshop side**

- [x] `scripts/task-queue.sh`: the restricted endpoint the development key is
      pinned to — seven verbs, no SQL, every value checked
- [x] `scripts/watch-tasks.sh`: waits on the queue in shell so the session only
      wakes when there is work
- [x] Docs: GUIDA 4.9 (key setup, allowed verbs, verification)
- [x] Verified: 18 checks including refused shell injection, refused SQL
      injection, and a Python-to-shell-to-Python round trip
- [x] Generate the dedicated key and install it on the server; `whoami` and
      `cat .env` both refused, `touch` and `list` work
- [x] Deploy step A and B to the VPS, service healthy, 12 messages preserved

**Step C — found after deploying, and the reason v0.3 could not have worked**

- [x] Fix: `GroqLanguageModel` ignored the `tools` argument entirely, so the
      model was never offered them on the provider used in production
- [x] Translate declarations, calls and results between the two dialects, in
      the adapter; router untouched
- [x] Keep tool traffic intact when replaying an agentic turn
- [x] Tests: 24 new, 115 total, ruff clean
- [x] Verified against the live API: prefix registers, missing capability is
      proposed not registered, status answered from the database
- [x] Deploy step C and push, verified against the live API on the deployed code

**Step D — closing the loop**

- [x] `scripts/queue-brief.sh` + a `SessionStart` hook, so a session opens
      knowing whether work is waiting rather than only if someone looks
- [x] `create` verb, for a defect found while working rather than commissioned;
      it still stops at checkpoint 1 and asks
- [x] The loop ran end to end for real: commissioned from Telegram, read from
      the queue with the restricted key, answered with checkpoint 1

**Step E — memory and tools, found damaging each other in production**

- [x] Fix: the queue listing led with the original request, so a model told to
      be brief kept the ambiguous half and dropped the clarification
- [x] Diagnosis: the tool was not being called at all (`tools=0`) — a stored
      answer was repeated word for word instead. Any tool reporting mutable
      state has this problem, not just this one
- [x] `ContextProvider` in `core/router.py`: state put in front of the model
      every turn rather than fetched when it remembers to. Provider-independent
      by design — plain text, no `tool_choice` to translate between dialects
- [x] `DevelopmentContext`, wired in `main.py`; router still knows nothing
      about tasks
- [x] Measured: 6/10 as found, 8/10 with the provider, 9/10 with a clean
      history, 10/10 with both, 5/5 on the deployed code
- [x] Poisoned history cleared, database backed up first so it stays reversible
- [x] Tests: 13 new, 132 total; docs, `REVISIONE.md` 17.10, deployed and pushed

**Commissioned, worked, closed**

- [x] #2 — the answer is no longer lost when Telegram drops a connection: the
      typing indicator is harmless, the send is retried on transient failures,
      and delivery is not all-or-nothing. Tests caught `BadRequest` inheriting
      from `NetworkError`, which had permanent rejections being retried
- [x] #1 — EMMA reports the commit she is running, `/health` carries it, and
      `scripts/deploy.sh` stamps it as part of deploying rather than as a step
      to remember. It refuses a dirty tree, failing tests, or an archive
      carrying `.env` or `data/`
- [x] Language boundary settled: English for what the model reads to decide,
      Italian for what the user sees — EMMA quotes tool output verbatim

**Open, not resolved**

- [x] A deploy on 31/08 at 13:34:58 could not be traced to the command that
      made it. Not explained, but no longer possible: `scripts/deploy.sh` is
      now the one way to deploy, it refuses an uncommitted tree, and it leaves
      the commit stamped on the server.
- [ ] Confirm closing job #1: the answer "sì, chiudi i lavori" was given on #2
      and read as covering both. The queue keeps no timestamp for an answer, so
      the order of events could not be checked.
- [ ] `/root/emma-pre-*` on the server: four safety copies from one day, worth
      pruning once the work they protect is settled.

**Later**

- [ ] Tools that serve the user rather than the mechanism: notes, reminders,
      web search
- [ ] Consider whether other mutable state deserves a `ContextProvider`. The
      failure that produced it was not specific to development jobs, so any
      future tool reporting something that changes inherits the same trap.

---

## v0.4.0 — Voice satellite (Raspberry Pi)

Goal: wake-word → STT → EMMA → TTS response on a Raspberry Pi, forwarding
to the same Telegram backend without changing the router.

- [ ] Spec the satellite adapter in `REVISIONE.md`
- [ ] Implement `adapters/voice.py` (wake word, STT, TTS)
- [ ] Integration tests (offline STT/TTS mocks)
- [ ] Deployment docs (`docs/GUIDA.md` new chapter)
- [ ] Backup + commit

---

## Backlog (no version assigned)

Ideas that need a `REVISIONE.md` entry before they can be scheduled:

- Rate-limiting / budget guard (cap daily Anthropic spend)
- Multi-user support (whitelist of user IDs instead of one)
- Webhook mode instead of long polling (needs an inbound port / reverse proxy)
- Structured output for tool responses
- Observability: metrics endpoint for Prometheus
