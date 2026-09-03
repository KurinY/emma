# Session Log

Tracks what each Claude Code session did, what was left pending, and notes
for the next session. Newest entry at the top.

---

## 2026-09-03 — Session 14

**Status:** Complete, in production (`36a54ab`).

**Context:** The user invoked `/superpowers:requesting-code-review`. I handed
the two-stage removal (`a3114df..45a455a`) to a separate reviewer, with context
built for the purpose instead of the session's history.

**It found three true things, and the first is instructive.**

1. **"Already off" was a counter, not evidence.** `disabled_at` was written and
   never read, and a turn allows five tool rounds: the model could switch a tool
   off and ask for its removal **in the same breath**. The code required two
   calls, the documents promised two occasions. `MIN_TIME_OFF_SECONDS` (one
   hour) was added. It is a small deviation from the user's design, which said
   "only if already disabled" without mentioning time — but time is what makes
   the reasoning he gave true.
2. **The gate was stale for the rest of the turn**, and the comment saying
   otherwise was one I had written myself: true before this feature, false
   **because of** it, since switching a tool off is itself a tool. Writing the
   test turned up a narrower hole — `remove_tool(x)` and `x` in the same round —
   so the refusal now consults the gate **at the moment of the call**.
3. **The router did not have a single test.** I had demonstrated it by hand in a
   throwaway script, that is, the kind of evidence that disappears. Now there is
   `tests/test_router_gate.py`, and `core/router.py` is at **100%**.

**Also fixed:** `PROTECTED` enforced in the store as well; no duplicate jobs; a
row that outlives the tool it names is no longer counted; the mixin refuses
instead of skipping validation; `EnableTool` no longer inherits a mixin it does
not use.

**One clarification where the review pressed too hard:** the second stage does
not delete code, it files a job that a human reads and that
`abandon_development` can remove.

**Verified in production, four steps:** it switches off; refuses immediately
afterwards saying *"riprova fra 60 minuti"* with zero open jobs; after a
simulated hour it files job #1; a fourth request reports the same number instead
of opening another.

**Numbers:** 490 tests (they were 466), `core/router.py` 100%, toolstate 97–99%.

---

## 2026-09-03 — Session 13

**Status:** Complete, in production (`ee56a0f`). Session resumed after a crash.

**Context:** Implemented entry 24 of `REVISIONE.md`, the two-stage tool removal
proposed by the user.

**The design is his, and it is better than the one I had proposed** (a variable
in `.env` read at startup). First request: the tool is **only disabled**. Second
request, and **only if it is still off**: the development job that takes it out
of the code is filed. "Already off" is not a formality, it is evidence — one has
done without it and did not miss it.

**The thing I expected to be expensive was not.** I believed the immediate
effect would require making the router's set of tools mutable. Instead the
router **already** built the declarations once per turn, so a `ToolGate`
protocol alongside `Tool` and `ContextProvider` was enough — `core/` still
imports nothing from `tools/`.

**Two switches for one door.** Hiding the declaration is what usually prevents
use; but a call may already be in flight from a turn in which the tool was still
offered, so there is also a refusal at execution. That is what turns the gate
from a strong suggestion into a guarantee.

**Not locking yourself out:** `list_tools` and `enable_tool` cannot be switched
off, and a test verifies that those two names **really exist** — a guard on a
misspelled name protects nothing. A disabled tool stays listed as
*(disattivato)*: one that vanished could no longer be asked to come back.

**A live test, four turns:** "che ore sono" → it works; "elimina current_time" →
**only disabled**, zero jobs; "che ore sono" → *"Al momento non ho piu' lo
strumento per l'orario. Vuoi riattivarlo?"* (I no longer have the clock tool.
Would you like it back?); "elimina current_time" → **job #1 filed**. The third
turn is better than the design: I only guaranteed that the tool would disappear;
that she would notice and offer to switch it back on, she worked out herself.

**Cost: +276 tokens per turn**, the highest so far — from ~65 to ~59 exchanges a
day. Stated plainly in `REVISIONE.md`: it is paid for on every message and used
rarely, so if the quota tightened these two are among the first candidates to
switch themselves off.

**Numbers:** 466 tests (they were 441), 25 new, `tools/toolstate` at 96%.

---

## 2026-09-02 — Session 12

**Status:** Complete, in production (`a3114df`).

**Context:** The user made rough edits to `prompts/system_prompt.txt` and asked
for a review, plus a rule about the tool list and an opinion on tool removal.

**The edit that had to be corrected.** His draft had EMMA declaring capabilities
that do not exist: *"hai accesso a internet solo su specifica richiesta... esegui
comandi... leggi file e controlli la casa"* — you have internet access on
request, you run commands, you read files and control the house. Verified:
**none of those four tools exists**. The old sentence was stale in its framing
but true in its content; the new one was false in the most dangerous direction,
because a model convinced it can search the web describes a search it does not
perform. Rewritten as capabilities *not yet given*, with the offer to file them.

**Other corrections:** a note addressed to Claude Code was inside the file the
model reads; an orphan bullet; two typos. And **a defect of my own from an hour
earlier**: the paragraph forbidding the recitation of tools from memory recited
them from memory — it would have drifted at the next tool exactly as the old
sentence did.

**The name removed**, at his request: `prompts/system_prompt.txt` is tracked and
public (rule 7). Removed from two test fixtures that contained it as well.

**Rule 9 added to `CLAUDE.md`**: the five places to update when the set of tools
changes, with the emphasis on the prompt, which is the only one without a guard
— eight tools were added while it still said *"non puoi eseguire comandi"* (you
cannot run commands), and the user noticed before any test did, because no test
was looking there.

**`REVISIONE.md` entry 24:** his proposal of two-stage removal (disable, then
remove only if already disabled) is better than mine with the `.env` variable.
The router builds the declarations **on every turn**, so filtering out disabled
tools costs a few lines and no restart.

**Verified in production:** *"cerca su internet le previsioni"* (search the web
for the forecast) → it calls nothing, invents nothing, offers to file a job.
*"come stai?"* → "Sto bene, grazie! E tu?" without reciting that it is a
program.

**Numbers:** 441 tests, ruff clean. Prompt: ~1,249 tokens (+25).

---

## 2026-09-02 — Session 11

**Status:** Complete, in production (`89b53c2`).

**Context:** An observation from the user: *"l'IA si rende conto di avere dei
tool ma non sempre, e in realta penso che i tool vengano integrati ma lui non se
ne renda conto"* — the AI realises it has tools but not always, and I think the
tools do get integrated but it does not notice. He asked to be able to know how
many tools she has, which ones, and the descriptions on request.

**They were two distinct problems, and the second was our fault.**

1. **She cannot enumerate them.** The declarations reach the model through the
   API's dedicated field — as callable functions, not as readable data — so
   listing them is not something a model does reliably about itself. Added
   `list_tools` in `tools/introspection.py`, which receives **the same tuple as
   the router**, itself included: a hand-written list would be a second place to
   update and the first to be forgotten. Two-phase construction, with a test
   verifying that the inventory really was filled — an empty one answers "I do
   not know" instead of breaking, so it would always be wrong and never shout.
2. **We were the ones telling her she could not.** The prompt still said
   *"Rispondi a domande e converso. Non hai accesso a internet, non puoi
   eseguire comandi..."* (I answer questions and converse. You have no internet
   access, you cannot run commands…) — accurate for v0.1, when the tool list was
   empty, and repeated in 39% of her context on every turn for the eight tools
   that had arrived since. **This is the most likely reason she seemed not to
   notice**, and it was a sentence of ours, not a limit of hers.

**Verified with the real model:** *"quanti tool hai?"* → calls `list_tools` →
*"Ho 9 strumenti disponibili."* *"elencami i tuoi strumenti e spiegami a cosa
serve ognuno"* → calls `list_tools(detailed=True)` and translates them into
Italian one by one. And she still chooses `current_time` for the time, so the
new tool has not confused the others.

**Cost:** +306 tokens per turn (183 the declaration, 123 the more honest
prompt): from ~72 to ~65 exchanges/day.

**Numbers:** 441 tests (they were 427), ruff clean.

---

## 2026-09-02 — Session 10

**Status:** Complete. Job #9 closed, in production (`320bc66`).

**Context:** A session woken by the watcher, not by the user: `new work (#9)`.
The watcher also cleared, by itself, a lock left by the previous session's
process (`clearing a lock left by pid 623, which is gone`) — the defence written
yesterday, at its first real opportunity.

**A correction from the user, and it holds for the future:** I had asked "shall
I do #9?". The answer: *"perche me l'hai chiesto? dovresti farlo senza chiedere
dal momento che ti ho svegliato con il watcher"* — why did you ask me? you
should do it without asking, given that I woke you with the watcher. **The queue
is the authorisation**: a job commissioned there is already approved, and asking
for confirmation makes the same gesture happen twice. This suspends, for jobs
arriving from the queue, the "Understood" gate of rule 8 in CLAUDE.md.

**Done: `tools/clock.py`** — the `current_time` tool. No network, no key, no new
dependency.

**Two ways of being silently wrong, found by trying it on both machines:**
1. **The time zone is the user's, not the server's.** Today they coincide, so
   reading the system clock would look correct — and would stay correct until
   somebody moved the machine. A named zone also brings daylight saving from the
   database instead of from a fixed offset: Italy is UTC+1 for half the year.
2. **The names do not come from the locale.** The service runs with `LC_ALL=C`,
   where `strftime('%A')` answers "Wednesday". The Italian days and months are
   written in the code, which also makes the output identical on every machine.

Windows has no time-zone database and the project does not depend on `tzdata`:
there it falls back to the system clock **and says so**, in the answer and in
the log.

**Verified with the real model in production:** it chooses `current_time` for
"che ore sono", "che giorno e'" and also for "fra quanti giorni e' il 10
settembre" — the question that does not ask for the time but needs it.

**Numbers:** 427 tests (they were 408), ruff clean. Cost: +104 tokens per turn.

**Note:** job #8 (weather) was abandoned by the user between the two sessions;
the queue is now empty.

---

## 2026-09-01 — Session 9

**Status:** Complete. The memory module in production (`95c277b`), verified
live.

**Context:** *"Vorrei che ci prendessimo un momento per capire come implementare
la memoria su emma. Le altre repo simili come hanno fatto?"* — let us take a
moment to work out how to implement memory in EMMA; how have similar
repositories done it? — then building, testing and releasing.

**The research, before writing code.** Three families: extraction+update with an
LLM (mem0), a temporal graph (Zep/Graphiti), memory inside the agent runtime
(Letta). A 2026 paper independently confirms the choice already made in entry 18
to reject summarising: *"fidelity before structure"*, verbatim chunks beat
extracted artefacts.

**The structural decision:** that literature solves a **retrieval** problem —
which of thousands of memories are needed now — which arises from many users and
unlimited history. EMMA has one user. Below ~150 facts it is better to inject
them all, and nothing can be retrieved badly if nothing is retrieved.
mem0-style extraction would halve her day: 42 exchanges against 84.

**Built:** `tools/facts/` — two tools (`remember_fact`, `forget_fact`), a
`FactsContext`, a table in the same SQLite file. No `recall`: it is all in the
context already. Installed, and uninstallable, with two lines in `main.py`.

**The two corrections that came from measurement:**
1. I had told the user +15%. The **declarations cost 303 tokens per turn**, paid
   even when unused: it starts at +13% with zero facts. The estimate had counted
   the facts and forgotten the tools.
2. `MAX_ACTIVE_FACTS` was 100 but the character cap lets ~80 in: the rest would
   have been stored, counted and never seen. Brought down to **50**.

**Verification in two stages, as the user asked:**
- The **357 pre-existing tests run untouched before wiring anything up**: all
  green. After wiring, exactly one failed, the one asserting the set of tools —
  the test doing its job.
- On the production database after the deployment: the `facts` table created,
  `PRAGMA integrity_check` = ok, **20 conversation messages intact**.
- Live against the real model: it records, and after a simulated restart with an
  empty conversation it answers *"Si chiama Sara."*

**Numbers:** 408 tests (they were 357), the new module at 100%, cost ~64
exchanges/day with thirty facts against 84 with no memory.

**Commits:** `6f00e1a` the module, `95c277b` the documentation.

---

## 2026-09-01 — Session 8

**Status:** Complete. v0.3.0 released and tagged.

**Context:** A continuation of Session 7. It began with a report from the user:
*"ho inserito un task ma tu non te ne sei reso conto"* — I put a job in and you
did not notice.

**The hole, and the three levels:** there was a single hook, `SessionStart`,
which looked at the queue on opening and never again. A session lasting hours
could not know that a job had arrived in the meantime. Now:
- `UserPromptSubmit` → `queue-brief.sh` (a job arrives, and then the user
  writes)
- `Stop` → `watch-tasks.sh` with `asyncRewake` (a job arrives, nobody writes)
- a local cache as a **fallback**, not as a source: the server first, always.

**Verified in the field**, not simulated: the watcher kept vigil for 80 minutes
in silence over the job already announced, and woke the session exactly once,
when a new job arrived.

**Five defects, all found by trying things or by the user:**
- A `break` inside a `case`: `case` is not a loop, so it exited the `while` and
  the watcher died after five seconds in silence.
- Exit code 2 inverted between the two modes: with `asyncRewake` it would have
  woken the session precisely when there was nothing to say.
- No memory of the announced ids: the `Stop` re-arm would have woken endlessly
  for the same job.
- `pipefail` made an **empty queue** and an **unreachable server**
  indistinguishable: with the queue just emptied, the script announced "5 jobs"
  read from the cache.
- **The scheduled task made a window flash** every 5 minutes. `-Hidden` hides
  the task in the list, not the window. No command I could run would have
  detected it: the user was the only measuring instrument. Disabled, not
  deleted; the guide now explains why not to install it.

**CHANGELOG:** the user noticed *"the next planned step is v0.3"* inside a
section describing v0.3 in the past tense. I put it down to a stale fetch:
**wrong**. `[Unreleased]` had two headings of every kind, because I was
inserting new entries under the first series without seeing the second. His
reading was right, my explanation was not. Now `[0.3.0] - 2026-09-01`, one
heading per kind, 29 entries moved and counted (29 in, 29 out).

**Tags created** (none existed): `v0.1.0` → `f4e6fbd`, `v0.2.0` → `c64177b`
retroactively, `v0.3.0` → `5a9a8e5`. The CHANGELOG's comparison links had been
pointing at non-existent references from the beginning.

**Not done, on explicit instruction:** job #8 (a weather tool). It stays in the
queue; the hook will carry on announcing it.

**Commits:**
- `748afe3` keep the queue watcher running for as long as the session is
- `18cbdb4` prefer the live queue, keep the cache for when it is not there
- `4ce564a` stop recommending a scheduled task that flashes a window
- `5a9a8e5` close 0.3.0 in the changelog, and merge the headings it had twice

---

## 2026-09-01 — Session 7

**Status:** Complete, pushed and in production (`15d2531`)

**Context:** A continuation of Session 6. An open mandate from the user:
*"continuare a lavorare sulla versione corrente e apportare tutte le modifiche
di miglioramento che ti vengono in mente."* — carry on working on the current
version and make every improvement you can think of. I looked for defects by
measuring, not by imagining: coverage, probes on the edge cases, and inspection
of what actually runs on the server.

**Defects found and fixed:**
- **`TelegramAdapter.stop()` chained three steps**, so the first one that raised
  skipped the other two and left the HTTP session and PTB's task queue half
  released. **The same defect as the lifespan's**, one level lower, and more
  likely there: on this host one connection in twenty to Telegram fails, and
  shutdown is exactly when it drops. Neither `start()` nor `stop()` had a test:
  `adapters/telegram.py` is now at 100%.
- **The bot can go deaf without dying.** If long polling stops while the process
  lives, nobody can reach her any more and `/health` answered `ok`. It is the
  symptom the user reported twice on 31 August. Now `/health` reports
  `telegram: listening|not polling` and goes to `503` — and since `backup.sh`
  queries it every night, the fault is found by a job that runs regardless.
- **The `VERSION` stamp could lie silently.** Copying a file with `scp` and
  restarting leaves the stamp declaring a commit that is no longer the one
  running — **a mistake I made twice** on 31 August, an hour after writing that
  the stamp had made the question answerable. At startup the installation is now
  compared with the deployment time and what has changed is reported.
- **Every backup manifest said `git commit: not a git repository`.** True and
  useless: an archive restored in six months' time could not be tied to a
  version. Now, when there is no checkout, it reads the `VERSION` stamp.
- **A missing provider package produced a traceback** instead of the line saying
  what to do. Now `MissingDependencyError`, reported like a wrong `.env`: exit
  2, no stack trace.
- **`_describe_age` said "less than a minute ago" of 89 seconds.** Text you
  read, passed on by a model that cannot check it. The threshold was fixed;
  which turned up three singular forms never handled ("1 minuti fa").
- **`.gitattributes` extended to every text file.** The working copy was half
  CRLF and half LF, which makes a byte-exact edit fail silently on one file and
  succeed on another: it cost me a wrong diagnosis tonight. `git add
  --renormalize` did not move a single piece of content.

**Verified on the server, not assumed:**
- The backup timer runs (last result `0`), the archives exist in
  `/var/backups/emma` at `0600`, and **the fallback to the primary disk works**
  (`/mnt/backup/emma` does not exist). Disk at 27%.
- A real archive opens, contains the DB snapshot and the `.env`, and excludes
  `.venv`, the caches and `data/`.

**Not done, deliberately:** splitting `core/llm.py` into three modules
(`REVISIONE.md` entry 20). Rule 2 of `CLAUDE.md` says not to implement an entry
from `REVISIONE.md` unless it is asked for by name.

**`REVISIONE.md` entry 21 added:** the router holds no lock between reading the
history and writing; it is safe only because PTB is at
`max_concurrent_updates=1`. Verified, not assumed. The per-conversation lock is
to be done as the first step of the voice satellite, before the second channel.

**The tail of the session — a defect introduced and fixed at once:** on the
first deployment the stamp check reported a file on an installation just made
(`.pytest_cache/v/cache/nodeids`). It was right about the fact and wrong in its
judgement: the deployment's remote step runs the suite on the server **after**
writing the stamp, so that cache is always newer than `built`. Every deployment
would have reported a drift that does not exist, and an alarm that always sounds
teaches you to ignore it precisely when it is right. Fixed and re-verified in
production: `modified_since_deploy: 0`.

**`REVISIONE.md` entry 22 added**, which emerged while investigating that false
positive: the deployment does `tar -xzf` over the tree, so it **never removes
anything**. A Python module deleted from the repository stays importable in
production — in development the import fails, on the server it works, executing
code that exists in no commit. The fix is `rsync --delete`, which rewrites the
road every release travels: to be done awake, rehearsed first.

**Numbers:** 357 tests (they were 324 at the start of the session), coverage
98%, `adapters/telegram.py` and `config.py` at 100%, ruff clean.

**Commits:**
- `3cc4101` make list-all list all of it
- `166fb98` close the session log: deploy, the two commissioned jobs, and two mistakes
- `2ebdc6e` stop the telegram adapter one step at a time, and test that it does
- `16a0d8a` answer a broken install with a sentence, not a stack trace
- `8a72c00` say a true thing about how long ago something was
- `0cc50e8` notice a bot that has gone deaf
- `504bdff` make the version stamp admit when it has gone stale
- `cc4b982` record in every backup which code it holds
- `43e56fe` store and check out every text file as LF
- `ff8cbc5` test the Anthropic dialect, which never had a test of its own
- `9a5bfd6` cover the two configuration mistakes nobody had tried

---

## 2026-08-31 — Session 6

**Status:** Complete (awaiting a decision on push and deployment)

**Context:** A continuation of Session 5. The final review for production, at
the user's request: *"Il progetto deve essere stabile quindi gestire
correttamente tutte le eccezioni. Il programma deve essere fluido il piu
possibile e il codice deve essere ordinato e deve seguire le buone norme di
produzione."* — the project has to be stable and therefore handle every
exception correctly; the program has to be as smooth as possible and the code
tidy and following good production practice. Carried out autonomously on an
explicit mandate (*"non disturbarmi finche non hai finito"* — do not disturb me
until you have finished), in three agreed phases: correctness, observability,
order.

**Done — phase 1, correctness (every boundary with the outside):**
- `core/memory.py`: five `assert`s replaced with `_require_open()` — they
  vanished under `python -O`, leaving an `AttributeError` on `None`.
  `core/tasks.py` already did it correctly: the two sibling modules disagreed.
- `core/retry.py` extracted: the backoff formula was repeated 5 times across 2
  modules.
- `main.py`: startup opened the two databases **before** the `try`, so a
  Telegram error left the connections open; and shutdown was sequential, so the
  first exception abandoned all the rest. Now exactly what was assembled is
  taken apart, in reverse order.
- **HTTP 429 treated as a permanent fault** (it was filed with the 4xx): a
  per-minute limit was never retried, and a daily ceiling was reported as *"non
  riesco a contattare il cervello, riprova tra poco"* — the wrong diagnosis and
  advice that could only fail. It is today's 17:32 incident. Now every client
  has a `RateLimitError` branch **before** `APIStatusError` (the same order, and
  the same reason, as `BadRequest` before `NetworkError` in the Telegram
  adapter); the server's `retry-after` is weighed against `remaining_backoff()`,
  and `LLMQuotaExceededError` carries the wait through to the user: *"Riprova
  fra circa 11 minuti."*
- **A database fault took the whole turn down**: the read at the start of the
  turn was outside the `try`, and the two writes at the end were unprotected.
  Now losing the history costs the context, not the turn; and failing to store
  an answer already paid for in tokens no longer throws it away.
- **An unforeseen fault produced silence**: PTB's error handler kept the process
  alive but said nothing to the user. Now it answers, with the same whitelist as
  every other path.

**Done — phase 2, observability:**
- The four ways a turn can degrade had four log formats and two severities. They
  now all go through a single `_degrade()`, share the line `turn degraded
  (<reason>)`, and the reason travels on the response.
- `TurnStats`: turn count / degraded turns / last reason / how long ago.
- **`/health` could only say "ok"**, even with a dead database. Now it really
  reads from the store (the same operation as every turn, far cheaper than
  `PRAGMA integrity_check`) and answers `503 degraded` when it cannot.

**Done — phase 3, order and coverage:**
- Tested the failure branches of the **self-repair** — a quarantine that cannot
  rename, a snapshot that will not copy, a failed `VACUUM`, a sick new snapshot,
  a refused `chmod`, a failed rotation. They were all taken on trust:
  `core/memory.py` 87% → 97%.
- Covered the connection/5xx branches of the **Groq** client, which had them
  only for Anthropic — the asymmetry that has already made the two clients
  diverge once.
- **263 tests** (they were 192 at the start of the session), `core/` at 97%,
  ruff clean.

**Docs:** `CHANGELOG.md`, `REVISIONE.md` (entry 19), `docs/GUIDA.md` +
`GUIDA.pdf` regenerated (41 pages; the front matter and footer were still at
`v0.2.0`).

**Commits from this session:**
- `d6b63bd` unwind a failed start-up, and cover the two modules that had no tests
- `0e5f7e3` treat a rate limit as its own kind of failure, not an outage
- `6675993` do not let the conversation store take the turn down with it
- `6667a57` answer the user even when the fault was one nobody foresaw
- `dbd25ba` make a degraded turn say why, and let /health report ill health
- `ec90095` record the observability work in the changelog
- `ca8050d` test the self-repair paths that only run on the worst day

**Done — the two delegated points ("do as you think best"):**
- **`core/llm.py`**: the two `except` ladders deduplicated, now in a
  `_RetryLadder` written once and parameterised on the SDK module. The two
  `complete()` methods drop from 107 and 81 lines to under 40, no per-provider
  clause survives, and the log formats stayed identical (verified line by line).
  I did **not** split the file: the obvious seam — 149 lines of Groq dialect
  with a dedicated test file — is not a seam, because those functions depend on
  the vocabulary that lives there (a circular import). A third module would be
  needed: `REVISIONE.md` entry 20, with the layout ready.
- **`/health`**: proposal C implemented. `scripts/backup.sh` queries it before
  writing the manifest; the outcome goes into the journal and the
  `MANIFEST.txt`, and it never makes the backup fail. Tested against a real
  server in all three states; the third found a defect in the code just written
  (`curl` already prints `000` by itself, and the fallback added a second one).
- **`.gitattributes`** added: developing on Windows and deploying with `git
  pull` on Linux is the arrangement in which a CRLF gets into a script that
  cannot survive it. A shebang with a carriage return fails without saying why.

**Done — deployment, the queue and the two commissioned jobs:**
- **Deployed to production**, verified: `/health` answers `ok`/`store ok` and
  declares the same commit that is really running.
- **Job #5 closed — it was not a fault.** In the database: `sei vivo?` → "Sono
  un assistente virtuale...". The turn was a `groq call ok`, stored in memory
  (degraded turns never are). It was the prompt asking for exactly that. Solved
  on the prompt side: concision applies to the work, not to the person asking
  for it. Measured: +172 tokens per turn, ~5 fewer exchanges/day against the
  Groq ceiling.
- **Job #6 done**: `abandon_development`, the fourth tool. It does not delete —
  the row stays `abandoned` with the reason, like the corrupt DB put in
  quarantine. Open jobs only. Verified against the real model in production:
  turn 1 asks for confirmation naming the job, turn 2 calls the tool, the queue
  is left with only the right job.
- **#3 and #4 abandoned** (a queue test; a duplicate).
- **`list-all` did not list all of it**: it filtered on open jobs, so with an
  empty queue it answered nothing and the history of decisions was reachable
  only by guessing an id.

**Two mistakes of mine, both found by trying rather than by rereading:**
- I wrote two instructions into the prompt that contradicted each other about
  confirming before abandoning. On the page they looked like a single sentence;
  the real model revealed it.
- Twice I copied a file to the server with `scp` instead of deploying,
  misaligning the `VERSION` stamp — exactly the defect that stamp exists to
  prevent, and which I had just finished solving. Fixed with a clean deployment
  both times.

**Pending — needs a decision from you:**
- [ ] The commit message of `0fb0b1b` still contains a Japanese character
      (`un試`): it can only be removed by rewriting history, which I do not do
      without an explicit request.
- [ ] `REVISIONE.md` entry 20: splitting `core/llm.py` into three modules. The
      deduplication is done; the split is correct but not urgent.

---

## 2026-08-31 — Session 5

**Status:** Complete

**Context:** Regenerating `docs/GUIDA.pdf` after a manual update to
`docs/GUIDA.md` (version 1, text only).

**Done:**
- `docs/GUIDA.md` updated by the user (version 1, text only)
- `docs/GUIDA.pdf` regenerated with pandoc + xelatex
- ROADMAP.md: GUIDA.pdf (v0.1.x) and the GUIDA.md update (v0.2) ticked off
- **A full review of the guide to bring it in line with v0.2.0** (the guide was
  still at v0.1.0 in many places):
  - front matter and footer: `v0.1.0` → `v0.2.0`, dated 31 August
  - intro and §1.9: two providers (paid Anthropic / free Groq)
  - §2.1 diagram: `data/emma.db` in the filesystem, memory.py = SQLite
  - §2.5: two memory implementations, no longer "SQLite planned"
  - §2.6: retry only on transient errors
  - §3.1 map: `data/` added, llm.py = Anthropic/Groq
  - §3.3: `SqliteConversationMemory` documented with open/close and
    MEMORY_DB_PATH
  - §3.4: two client classes with the same interface
  - §3.7: `SqliteConversationMemory` in main.py, the lifespan opens/closes the DB
  - §3.8: 21 → 43 tests, with a per-file table
  - §4.3/4.4/4.5/4.6: aiosqlite and groq in the pip check, `MEMORY_DB_PATH`
    among the optional values, the startup log with `provider=` and `db=`,
    `/health` with the provider
  - §5.1/5.4/5.5: persistent memory, zero cost with Groq, the "memory lost on
    restart" limit removed
  - **new §5.6**: how to clear the memory
  - §6.1/6.5/6.6.2/6.7: logs with the provider, a backup that includes the DB, a
    restore that brings the conversations back, **new SQLite troubleshooting
    cases** (permissions, database locked, unable to open) and Groq (404 on the
    model)
  - Appendices A and C: commands to clear the memory and inspect the DB
- `CHANGELOG.md`: `[Unreleased]` promoted to `[0.2.0] - 2026-08-31`, comparison
  links updated, a Documentation section added
- 43 tests green, ruff clean

**Done (continued) — database integrity (v0.2.1):**
- A question from the user: a backup of the DB alone + an automatic restore if
  it does not come back up. The analysis is in `REVISIONE.md` entry 16: the
  backup was **genuinely broken** (`tar` of a live SQLite can archive a
  half-finished transaction), while the generic automatic mirror was advised
  against and not implemented.
- `backup.sh`: a `VACUUM INTO` snapshot + an integrity check, `data/` excluded
  from the tar, `MANIFEST.txt` declaring the database's state. Requires
  `sqlite3`.
- `core/memory.py`: `journal_mode=WAL`, `integrity_check` on opening,
  quarantine of the broken file (never deleted), restoration from the most
  recent healthy snapshot with a fallback to the previous generation, snapshots
  on open and close. All logged at ERROR level.
- **A design constraint:** the restore triggers only on established corruption,
  never because "the service does not start" (the reasoning is in
  `REVISIONE.md` 16.5).
- 8 new tests (51 in total), ruff clean
- `backup.sh` verified end to end with a `sqlite3` shim on Windows: the happy
  path, the fallback without sqlite3, the exclusion of `data/`, and a message
  that really can be read back out of the archive
- Docs: GUIDA §1.4, §3.3 (a new self-repair section), §4.8, §5.6, §6.1, §6.5,
  §6.6.2, §6.7 (a new case), Appendices A/B; CHANGELOG; ROADMAP v0.2.1

**Done (continued) — the pre-release review:**

A systematic review at the user's request ("make sure everything is right to be
published and installed"). Three bugs found, two of which **blocked a clean
installation**:

1. **`emma.service` could not write the database.** `ProtectSystem=strict`
   without `ReadWritePaths` makes `/opt/emma` read-only: the published v0.2.0
   **failed on an installation made by following the guide**. It worked on the
   VPS only because there the unit had been written by hand in simplified form
   during the deployment. `ReadWritePaths=/opt/emma/data` added (the
   installation directory stays read-only, by choice).
2. **`emma-backup.service` could not read the database.** A WAL reader has to be
   able to update the `-shm` file: the archive would have come out with no
   history. `ReadWritePaths` extended to the database's directory.
3. **`backup.sh` got an absolute `MEMORY_DB_PATH` wrong** — it always prefixed
   the project directory, while `config.py` honours absolute paths. The result:
   an archive with no history, declared as "nothing to snapshot". Verified with
   an A/B test on the code before and after the fix.

Also: a speaking error naming `ReadWritePaths` instead of a raw `OSError`;
`data/` created in the guide at §4.6 before the unit (systemd refuses to start
if `ReadWritePaths` does not exist); an upgrade note from <0.2.1; recopying the
units in procedure 6.3; a stale header in `requirements.txt`.

Checks: 51 tests green, ruff clean, every module imported, a cold start (the
directory created, the history written, the snapshot present), `backup.sh` end
to end on a relative and an absolute path.

**Done (continued) — v0.2.1 deployed to production:**

Deployed to the VPS (IPv6-only, the code via tar+scp: GitHub is not reachable).
A safety copy of `.env` + `data/` + the previous unit before touching anything.
The deployment archive built excluding `.env` and `data/`, verified before
sending. `sqlite3` installed, `700` permissions on `data/` and `600` on the db,
**the hardened systemd units installed in place of the simplified manual one**.

Verified in production:
- 51→52 tests green on the server, the service `active`, 0 restarts
- logs with `provider=groq`, `db=/opt/emma/data/emma.db`; `/health` with the
  provider
- **a snapshot created at startup** — the proof that the `ReadWritePaths` fix
  works: with the previous unit it would have been impossible
- `journal_mode=wal` active, history preserved (8 messages, integrity `ok`)
- `backup.sh` end to end: a consistent and verified snapshot, `data/` excluded,
  `.env` included, the archive at `600`, the service alive during the backup

Two further defects found **during** the deployment and fixed:
1. **The snapshot at `0644`** — `VACUUM INTO` uses the process umask, so a file
   with the same conversations as the database came out more permissive than the
   database itself. Now `chmod 600` before the rotation (+1 test, 52 in total).
2. **26 MB of pip cache in every archive** — `/opt/emma` is also the `emma`
   user's home, so `~/.cache/pip` ended up in the `tar`. Excluded: the
   production archive went from **23 MB to 340 KB**.

**Done (continued) — backups with an automatic fallback:**

The request: back up to the secondary disk if there is one, to the primary
otherwise, but **it must happen regardless**. Implemented in `backup.sh`:
- an explicit `BACKUP_DIR` (environment or `.env`) → honoured as it is, with a
  fallback only if it is not writable
- no `BACKUP_DIR` → `/mnt/backup/emma` **only if it really is a separate
  filesystem** (comparing the device with `/`), otherwise `/var/backups/emma`
- the choice and the reason go into the log and the `MANIFEST.txt`
- the check is on the mount, not on the directory's existence: writing into an
  unmounted `/mnt/backup` would fill the system disk and those archives would
  disappear under the mount the day the disk was attached
- `--dry-run` no longer creates anything (before, the fallback would `mkdir`)

Corrections to the unit, both necessary for the fallback to work:
- removed `RequiresMountsFor=/mnt/backup`, which turned the absence of the disk
  into a failed job — the opposite of the guarantee requested
- `ReadWritePaths=-/mnt/backup /var/backups /opt/emma/data` (the `-` makes the
  first optional: without it, systemd refuses to start if it does not exist)
- `ExecStartPre=+/usr/bin/install -d -o emma -g emma -m 0700 /var/backups/emma`:
  `/var/backups` belongs to root, so the `emma` user could not create anything
  inside it and the fallback was unreachable on a freshly installed machine

Verified in production starting from a non-existent `/var/backups/emma`: the
service created it `emma:emma 0700`, the backup happened (343 KB), the manifest
declares the destination and the reason, the history is recoverable (integrity
`ok`). Separate-disk detection tested with `/dev/shm`. **Timer enabled**, next
run at 03:37.

**Confirmed by the user:** EMMA answers on Telegram after the move to the
hardened unit (the database went from 8 to 12 messages during the session).

**Done (continued) — v0.3: EMMA commissions her own development:**

Designed in conversation and written into `REVISIONE.md` entry 17 before
touching code. The user's constraints: one bot only, no additional spending,
EMMA never speaks first, consent at every step, and **no API key** — the
development side is a Claude Code session open on the PC, not a service.

- `core/tasks.py` + `tools/development.py`: a six-stage queue and three tools
  (`request_development`, `work_status`, `answer_question`). **The first tools
  ever registered on the router, and `core/router.py` did not change by one
  line** — the protocol written in v0.1 against an empty list held.
- `scripts/task-queue.sh`: the only thing the dedicated key can run, bound with
  `command=` in `authorized_keys`. Seven verbs, never SQL.
- `scripts/watch-tasks.sh`: it waits in shell, so the session wakes only when
  there is work.
- Deployed to the VPS: the restricted key installed and verified (`whoami` and
  `cat .env` **refused**), 12 messages preserved, 91 tests green on the server.

**The bug that made it all inert.** After the deployment, while inspecting the
code: `GroqLanguageModel` accepted the `tools` parameter and **never used it**.
Born in v0.1.x when the list was empty, the defect cost nothing; with three
tools registered it meant the model could not even see them. No error, no log:
EMMA answered in words, indistinguishable from correct behaviour unless you go
looking for the call that did not happen.

Fixed by translating the two dialects in both directions **inside the adapter**,
where the difference belongs: declarations, calls and results change shape, the
router carries on speaking one language. The treacherous piece was the replay of
the agentic turn, which flattened the tool traffic into prose — leaving the
model unable to see that it had called anything.

Verified against the real API, in an isolated directory on the server without
touching production: the `sviluppo:` prefix files a job, a missing capability is
**offered and not filed**, a status question answered by reading the database.

115 tests green, ruff clean.

**Done (continued) — step B3 and the first real turn of the cycle:**
- The `SessionStart` hook (`scripts/queue-brief.sh` +
  `.claude/settings.local.json`): counts the waiting jobs at the opening of
  every session. It reports only the number, not the text. It stays quiet and
  exits 0 if the server is unreachable.
- `scripts/task-queue.sh`: the `create` verb added — there was nowhere to put a
  defect found while working on the code. It does not move control: it still
  stops at checkpoint 1.
- **The first real turn**: the user commissioned job #1 from Telegram at 10:21,
  I read it from the queue with the restricted key and gave him checkpoint 1.
  The mechanism works end to end.

**A production incident (31/08, 13:17) — EMMA was not answering:**

The symptom: the service `active`, `incoming message` in the logs, no
`answered`. The cause: `httpcore.ConnectTimeout` on `connect_tcp` — the process
was not opening **new** connections to Telegram, while the long-polling one,
already established, was working. That is why messages came in and answers did
not go out.

Everything else ruled out before acting: the database intact, Groq reachable,
`curl` as the `emma` user at 0.11s, file descriptors 14 out of 1024. A stuck
httpx connection pool → **solved with a restart**.

Two lessons recorded in `docs/GUIDA.md` (a new case in 6.7):
1. **Measure with the right window.** I had concluded that the restart had not
   worked, using `--since "10 min ago"`, which reached back to before the
   restart and recounted the old errors. With `ActiveEnterTimestamp`: zero
   timeouts. My mistake, corrected.
2. **An underlying fragility found by measuring:** of 20 connections to
   Telegram, one fails. The VPS has a single IPv6 address and no IPv4 to fall
   back on. With the current code a failed send kills the turn silently, so ~1
   message in 20 would go unanswered. Filed as job #2.

**Done (continued) — the most instructive defect of the day:**

The user asked EMMA which jobs were outstanding. She reported **one of the
two**, describing it with the interpretation he had explicitly rejected. Two
distinct causes, found one at a time:

**The first cause (fixed, but it was not the right one).** The tool put the
original request *before* the clarifying question. A model whose prompt orders
it to be concise compresses, and in compressing keeps the beginning — so it kept
the user's ambiguous words and threw away the clarification. Fixed: the question
first, the shortened request after, plus an explicit instruction not to
summarise. Commit `f0ad40a`.

**The second cause, the real one.** In the logs: `tools=0`. **The tool was not
being called at all.** EMMA was repeating word for word a wrong answer given a
quarter of an hour earlier and stored in the persistent memory.

It is the interaction between two things that worked individually: **the memory
(v0.2) and the tools (v0.3) damage each other.** An answer derived from a tool,
once stored, is indistinguishable from a fact, and at the next question it is
reused instead of asking again. It is not specific to jobs: it holds for any
tool that reports changing state. **The tests could not see it, because they
test the pieces separately.**

**Measured**, ten attempts per configuration, the same question:

| Configuration | Correct |
| --- | --- |
| poisoned, no context | 6/10 |
| poisoned + context | 8/10 |
| clean, no context | 9/10 |
| clean + context | **10/10** |
| in production after the deployment | 5/5 |

**The solution, at the user's explicit request not to depend on the model**
(*"dobbiamo pensare che l'ia possa essere diversa alla base"* — we have to
assume the AI underneath may be a different one): `ContextProvider` in
`core/router.py`. A protocol with one asynchronous method, queried **once per
turn** (not on every tool round: the state does not change mid-turn), whose
result is appended to the system prompt. `DevelopmentContext` produces the line
with the counts and numbers, and declares which source wins when the memory
disagrees.

No decision is left to get wrong: the line is there regardless. And it is plain
text, so there is no `tool_choice` to translate between the two dialects —
changing provider does not silently degrade the behaviour. `core/` carries on
not knowing what a job is. A provider that explodes is logged and skipped.

Rejected: forcing `tool_choice` (it would require recognising "this is a status
question" without a model: keyword matching, fragile and language-bound) and not
storing tool answers in memory (it removes the poison but also the continuity).
The full reasoning is in `REVISIONE.md` 17.10.

13 new tests (132 in total), commit `6c07059`, deployed and verified.

**The history cleaned** with a full safety copy in
`/root/emma-pre-ctx-20260831-140320` (20 messages, 2 jobs): the deletion remains
undoable. The two snapshots were removed too, since they held the same history
and would have brought it back on any recovery.

**Done (continued) — the two commissioned jobs, closed:**

The cycle turned all the way round and in both directions: the user answered
from Telegram, EMMA recorded it, the watcher woke me (`work waiting - waking the
session`), I worked, and the closures went back to him by the same route.

**Job #2 — the answer is no longer lost** (commit `1960d20`). The "typing"
indicator was the first outbound call: a disturbance there killed the turn
*before* consulting the model. It is now harmless. Sending is retried on
transient failures (3 attempts, 1s then 2s, the policy from `core/llm.py`) and
delivery is no longer all-or-nothing: if one piece of a long answer is lost, the
others go out. If nothing arrives it is an explicit `ERROR`, not silence.

> **A trap found by the tests:** in python-telegram-bot **`BadRequest` inherits
> from `NetworkError`**, so `except (TimedOut, NetworkError)` — written to mean
> "transient only" — retried three times messages that Telegram will always
> reject. The permanent clause now comes first, with a comment explaining why
> the order is not cosmetic.

**Job #1 — EMMA knows which code she is running** (commits `8e71d21`,
`cfaa54e`). `core/version.py` prefers the stamp written by the deployment, falls
back to git on a checkout, and **says so when it does not know** instead of
inventing. `/health` exposes `version`, `commit`, `built`; `main.py` no longer
declares a version of its own.

**`scripts/deploy.sh`**, decided with the user in place of the minimal variant:
the stamp is not a step to remember, the deployment itself writes it. It refuses
to run if the tree is dirty (the stamped commit would lie), if the tests or ruff
fail, or if the archive contains `.env` or `data/` — checked twice. The first
real deployment went through it: **21 seconds, one command**, and production and
repository now match verifiably (`cfaa54e` on both sides).

**Language made consistent** (commit `cfaa54e`): English for what the model
reads in order to decide (names, descriptions, arguments), Italian for what
reaches the user. The boundary is not where it appears: EMMA **quotes the tool
strings verbatim** — `ATTENDE UNA RISPOSTA DELL'UTENTE` appeared word for word
in the chat — so translating them would put English fragments in front of an
Italian user.

**156 tests green.**

**Confirmed in production a few hours later.** At 14:36 a network degradation on
the VPS (Groq too, from <1s to 17–25s per call) made sending fail twice:

```
14:36:41  telegram send failed (attempt 1/3): TimedOut
14:36:47  telegram send failed (attempt 2/3): TimedOut
14:36:49  telegram send succeeded on attempt 3
14:36:49  answered chat_id=... (60 chars)
```

With this morning's code that message would have vanished silently and the user
would have seen a dead bot. The third network degradation of the day: this
host's fragility is recurrent, and it now costs a few seconds instead of an
answer.

**Pending:** none. The closure of both jobs is confirmed by the user: his plural
answer covered them both.
- [ ] **Traceability:** at 13:34:58 `tools/development.py` and
      `prompts/system_prompt.txt` reached production (the content correct, the
      fingerprints verified) **without my being able to name the command that
      did it**. The sequence was backwards: in production first, committed
      afterwards. Recorded so that it does not disappear, not because it has
      been solved.
- [ ] **Safety copies on the server** to prune: there are four `/root/emma-pre-*`,
      all from today.
- [ ] **EMMA has lost the conversational context** (the history is at zero).
      Intended, but worth knowing: to an "and so?" she no longer knows what was
      being referred to.

---

## 2026-08-30 — Session 4

**Status:** Complete

**Context:** A continuation of Session 3. Goal: v0.2, persistent SQLite memory.

**Done:**
- `SqliteConversationMemory` in `core/memory.py` through `aiosqlite`
- `MEMORY_DB_PATH` in `config.py`, `.env.example`, `docs/GUIDA.md` (Appendix B)
- `main.py`: swapped from InMemory to Sqlite, open/close in the lifespan
- `aiosqlite==0.20.0` in `requirements.txt`; `data/` in `.gitignore`
- `tests/test_memory_sqlite.py`: 9 tests including persistence across reopen
- All 43 tests pass, ruff clean
- README, ROADMAP and the repo About updated; authorship declared
- Backup `emma-20260830-230713.zip`, commit `016bbec`, pushed to GitHub
- Deployed to the VPS: service restarted, persistent memory **verified via
  Telegram**

**Pending:**
- [ ] Regenerate `docs/GUIDA.pdf` (manual — PDF toolchain)

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
- Telegram test passed — EMMA answers correctly through Groq
  (`openai/gpt-oss-120b`)
- IP addresses, hostnames and a personal name anonymised across every tracked
  file
- Rule 7 added to CLAUDE.md: a mandatory privacy check before every push
- `docs/GUIDA.md` updated: section 2.8 and Appendix B with all the new variables
- Backup: `D:\EmmaBackups\emma-20260830-170129.zip`
- Commit: `97cfe8a`

**Done (continued):**
- Deployment to the VPS (IPv6-only) completed: code copied via scp, Python 3.12,
  venv, .env, systemd service. EMMA answers on Telegram from the production
  server.
- README.md updated: multi-provider, Python 3.11/3.12 compatibility, layout
- ROADMAP.md updated with every completed v0.1.x task
- Pushed to GitHub (commit `c8a8c5a` + repo/roadmap updates)

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
- Set up GitHub repo, gh CLI authenticated, push policy clarified
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
