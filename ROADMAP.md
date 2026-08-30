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

## v0.3.0 — Real tools

Goal: EMMA can look things up and take actions. Each tool is a Python class
implementing the `Tool` protocol in `core/router.py`.

- [ ] Decide and document the first tool set (candidates in `REVISIONE.md`)
- [ ] Implement `tools/` package with at least one tool (e.g. web search or notes)
- [ ] Register tools in `main.py` and verify the router's agentic loop handles them
- [ ] Tests for each tool (offline mocks)
- [ ] Update docs, CHANGELOG, ROADMAP
- [ ] Backup + commit

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
