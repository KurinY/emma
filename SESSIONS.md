# Session Log

Tracks what each Claude Code session did, what was left pending, and notes
for the next session. Newest entry at the top.

---

## 2026-08-29 — Session 2

**Status:** In progress

**Context:** Continuation of Session 1 (context window ran out). Starting with
end-of-session procedure that was not completed.

**Done:**
- Created SESSIONS.md and ROADMAP.md for cross-session tracking
- Updated project_emma memory entry (default model was stale: haiku → sonnet)
- Ran end-of-session procedure: ruff clean, 33 tests passing, backup written to
  `D:\EmmaBackups\emma-20260829-120447.zip`, initial git commit `f4e6fbd`

**Pending:**
- [ ] Regenerate `docs/GUIDA.pdf` from `docs/GUIDA.md` (user must do this manually)

---

## 2026-08-29 — Session 1

**Status:** Complete (end-of-session procedure pending — context ran out)

**Starting point:** v0.1.0 freshly released. No Python environment on the dev
machine. Two known bugs identified during review.

**Done:**
- Set up Python 3.11 local venv via `uv` (installed with winget; PATH reloaded
  from registry)
- **Fix:** `core/llm.py` — split single `except AnthropicError` into three
  handlers; permanent 4xx errors (wrong key, bad request) now raise immediately
  instead of burning 3 s on pointless retries
- **Fix:** `adapters/telegram.py` `_split_message` — blank lines (`\n\n`) near
  a chunk-split boundary are now preserved in the next chunk instead of being
  silently dropped by the old `lstrip("\n")` approach
- **Added:** `tests/test_llm.py` — 6 tests covering retry / no-retry
  distinction (TDD: tests written first, confirmed failing, then code fixed)
- **Added:** `tests/test_telegram.py` — 6 tests for `_split_message` including
  blank-line preservation (same TDD flow)
- **Changed:** default model `claude-haiku-4-5-20251001` → `claude-sonnet-4-6`
  in `config.py`, `.env.example`, `docs/GUIDA.md` (section 2.8, variable
  tables, cost section, log examples), `CHANGELOG.md`
- All 33 tests passing; ruff clean (format + check)

**Not done (context ran out before end-of-session procedure):**
- [ ] Backup script: `powershell -ExecutionPolicy Bypass -File .\scripts\backup-dev.ps1`
- [ ] Git commit covering all changes above
- [ ] Regenerate `docs/GUIDA.pdf` (user must do this — PDF toolchain not
  available in Claude Code)

**Files changed this session:**
- `core/llm.py`
- `adapters/telegram.py`
- `tests/test_llm.py` (new)
- `tests/test_telegram.py` (new)
- `config.py`
- `.env.example`
- `docs/GUIDA.md`
- `CHANGELOG.md`
