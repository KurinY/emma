# Session Log

Tracks what each Claude Code session did, what was left pending, and notes
for the next session. Newest entry at the top.

---

## 2026-08-31 — Session 5

**Status:** Complete

**Context:** Rigenerazione `docs/GUIDA.pdf` dopo aggiornamento manuale di `docs/GUIDA.md` (versione 1, solo testo).

**Done:**
- `docs/GUIDA.md` aggiornato dall'utente (versione 1, solo testo)
- `docs/GUIDA.pdf` rigenerato con pandoc + xelatex
- ROADMAP.md: spuntati GUIDA.pdf (v0.1.x) e GUIDA.md update (v0.2)
- **Revisione completa della guida per allinearla a v0.2.0** (la guida era ancora
  ferma a v0.1.0 in molti punti):
  - frontmatter e footer: `v0.1.0` → `v0.2.0`, data 31 agosto
  - intro e cap. 1.9: due provider (Anthropic a pagamento / Groq gratuito)
  - cap. 2.1 diagramma: `data/emma.db` nel filesystem, memory.py = SQLite
  - cap. 2.5: due implementazioni di memoria, non più "SQLite in programma"
  - cap. 2.6: retry solo sugli errori transitori
  - cap. 3.1 mappa: aggiunta `data/`, llm.py = Anthropic/Groq
  - cap. 3.3: documentata `SqliteConversationMemory` con open/close e MEMORY_DB_PATH
  - cap. 3.4: due classi client con la stessa interfaccia
  - cap. 3.7: `SqliteConversationMemory` in main.py, lifespan apre/chiude il DB
  - cap. 3.8: 21 → 43 test, con tabella per file
  - cap. 4.3/4.4/4.5/4.6: aiosqlite e groq nella verifica pip, `MEMORY_DB_PATH`
    negli opzionali, log di avvio con `provider=` e `db=`, `/health` con provider
  - cap. 5.1/5.4/5.5: memoria persistente, costo zero con Groq, rimosso il
    limite "memoria persa al riavvio"
  - **nuovo cap. 5.6**: come azzerare la memoria
  - cap. 6.1/6.5/6.6.2/6.7: log con provider, backup che include il DB,
    ripristino che riporta le conversazioni, **nuovi casi di troubleshooting
    SQLite** (permessi, database locked, unable to open) e Groq (404 modello)
  - Appendici A e C: comandi per azzerare la memoria e ispezionare il DB
- `CHANGELOG.md`: `[Unreleased]` promosso a `[0.2.0] - 2026-08-31`, link di
  confronto aggiornati, sezione Documentation aggiunta
- 43 test verdi, ruff pulito

**Done (continued) — integrità database (v0.2.1):**
- Domanda dell'utente: backup del solo DB + ripristino automatico se non riparte.
  Analisi in `REVISIONE.md` voce 16: il backup era **realmente rotto**
  (`tar` di un SQLite vivo può archiviare una transazione a metà), il mirror
  automatico generico invece è stato sconsigliato e non implementato.
- `backup.sh`: snapshot con `VACUUM INTO` + verifica integrità, `data/` escluso
  dal tar, `MANIFEST.txt` dichiara lo stato del database. Richiede `sqlite3`.
- `core/memory.py`: `journal_mode=WAL`, `integrity_check` all'apertura,
  quarantena del file rotto (mai cancellato), ripristino dallo snapshot più
  recente sano con fallback alla generazione precedente, snapshot su open e
  close. Tutto loggato a livello ERROR.
- **Vincolo di progetto:** il ripristino scatta solo su corruzione accertata,
  mai perché "il servizio non parte" (motivazione in `REVISIONE.md` 16.5).
- 8 nuovi test (51 totali), ruff pulito
- `backup.sh` verificato end-to-end con uno shim `sqlite3` su Windows:
  percorso felice, fallback senza sqlite3, esclusione di `data/`, e messaggio
  effettivamente rileggibile dall'archivio
- Docs: GUIDA cap. 1.4, 3.3 (nuova sezione auto-riparazione), 4.8, 5.6, 6.1,
  6.5, 6.6.2, 6.7 (nuovo caso), Appendici A/B; CHANGELOG; ROADMAP v0.2.1

**Done (continued) — revisione pre-pubblicazione:**

Revisione sistematica su richiesta ("assicurati che sia tutto giusto ad essere
pubblicato ed installato"). Trovati tre bug, due dei quali **bloccavano
l'installazione pulita**:

1. **`emma.service` non poteva scrivere il database.** `ProtectSystem=strict`
   senza `ReadWritePaths` rende `/opt/emma` in sola lettura: la v0.2.0
   pubblicata **falliva su un'installazione fatta seguendo la guida**.
   Funzionava su Aruba solo perché lì la unit era stata scritta a mano
   semplificata durante il deploy. Aggiunto `ReadWritePaths=/opt/emma/data`
   (la directory di installazione resta in sola lettura, per scelta).
2. **`emma-backup.service` non poteva leggere il database.** Un lettore WAL deve
   poter aggiornare il file `-shm`: l'archivio sarebbe uscito senza cronologia.
   `ReadWritePaths` esteso alla directory del database.
3. **`backup.sh` sbagliava un `MEMORY_DB_PATH` assoluto** — prefissava sempre
   la project dir mentre `config.py` onora gli assoluti. Risultato: archivio
   senza cronologia, dichiarata come "nothing to snapshot". Verificato con un
   test A/B sul codice pre e post fix.

Inoltre: errore parlante che nomina `ReadWritePaths` invece di un `OSError`
grezzo; `data/` creata in guida al 4.6 prima della unit (systemd rifiuta di
partire se `ReadWritePaths` non esiste); nota di aggiornamento da <0.2.1;
ricopia delle unit nella procedura 6.3; intestazione stantia in `requirements.txt`.

Verifiche: 51 test verdi, ruff pulito, import di tutti i moduli, avvio a freddo
(directory creata, cronologia scritta, snapshot presente), `backup.sh`
end-to-end su percorso relativo e assoluto.

**Pending:**
- [ ] Deploy di v0.2.1 sul VPS Aruba e verifica. Richiede tre passi in più:
      `apt install sqlite3`, `mkdir -p /opt/emma/data && chmod 700`, e la
      **ricopia delle unit systemd** (quella su Aruba è la versione manuale
      semplificata, senza hardening).

---

## 2026-08-30 — Session 4

**Status:** Complete

**Context:** Continuation di Session 3. Obiettivo: v0.2 memoria persistente SQLite.

**Done:**
- `SqliteConversationMemory` in `core/memory.py` tramite `aiosqlite`
- `MEMORY_DB_PATH` in `config.py`, `.env.example`, `docs/GUIDA.md` (Appendice B)
- `main.py`: swap da InMemory a Sqlite, open/close nel lifespan
- `aiosqlite==0.20.0` in `requirements.txt`; `data/` in `.gitignore`
- `tests/test_memory_sqlite.py`: 9 test incluso persistence-across-reopen
- 43 test totali passano, ruff pulito
- README, ROADMAP, repo About aggiornati; authorship dichiarata
- Backup `emma-20260830-230713.zip`, commit `016bbec`, push GitHub
- Deploy su Aruba VPS: servizio riavviato, memoria persistente **verificata via Telegram**

**Pending:**
- [ ] Rigenerare `docs/GUIDA.pdf` (manuale — toolchain PDF)

---

## 2026-08-30 — Session 3

**Status:** Complete

**Context:** Continuation of Session 2. Goal: implement selectable LLM provider
(Anthropic / Groq) so EMMA can run on the free Groq tier.

**Done:**
- Added `GroqLanguageModel` to `core/llm.py` (OpenAI-compatible, same retry policy)
- Extended `config.py` with `LLM_PROVIDER`, `GROQ_API_KEY`, `GROQ_MODEL` support
- Updated `main.py` to select provider at boot; `/health` now exposes `"provider"`
- Updated `.env.example` with all new variables and documentation
- Added `groq==0.15.0` to `requirements.txt`
- All 33 tests passing; ruff clean
- **VM deployment**: Ubuntu Server VM (local test machine) created and running.
  Copied updated code to VM via scp, installed groq package, configured `.env`
  with `LLM_PROVIDER=groq` and Groq API key. Service confirmed starting with
  `provider=groq, model=openai/gpt-oss-120b`.
- Updated CHANGELOG.md with Groq provider entry

**Done (continued):**
- Telegram test passed — EMMA risponde correttamente tramite Groq (`openai/gpt-oss-120b`)
- Anonimizzati IP, hostname e nome personale da tutti i file tracciati
- Aggiunta Regola 7 in CLAUDE.md: privacy check obbligatorio prima di ogni push
- Aggiornato `docs/GUIDA.md`: sezione 2.8 e Appendice B con tutte le nuove variabili
- Backup: `D:\EmmaBackups\emma-20260830-170129.zip`
- Commit: `97cfe8a`

**Done (continued):**
- Deploy su VPS Aruba (solo IPv6) completato: codice copiato via scp, Python 3.12,
  venv, .env, systemd service. EMMA risponde su Telegram dal server di produzione.
- README.md aggiornato: multi-provider, compatibilità Python 3.11/3.12, layout
- ROADMAP.md aggiornato con tutti i task v0.1.x completati
- Push a GitHub (commit `c8a8c5a` + aggiornamenti repo/roadmap)

**Pending:**
- [ ] Regenerate `docs/GUIDA.pdf` (user must do this manually — PDF toolchain)

---

## 2026-08-29 — Session 2

**Status:** Complete

**Context:** Continuation of Session 1 (context window ran out). Starting with
end-of-session procedure that was not completed.

**Done:**
- Created SESSIONS.md and ROADMAP.md for cross-session tracking
- Updated project_emma memory entry (default model was stale: haiku → sonnet)
- Ran end-of-session procedure: ruff clean, 33 tests passing, backup written to
  `D:\EmmaBackups\emma-20260829-120447.zip`, initial git commit `f4e6fbd`
- Set up GitHub repo (KurinY/emma), gh CLI authenticated, push policy clarified
- **Decision:** No remote server for now. Deployment will be tested on a local
  VM (Ubuntu Server) on this Windows PC first.

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
