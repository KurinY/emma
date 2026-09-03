# Standing instructions for AI assistants

This file is read automatically by Claude Code, Cowork and similar assistants
working in this repository. It is not a style guide — `CONTRIBUTING.md` is —
and it is not documentation. It contains the few rules that must hold in every
session, because they protect work that a single careless session could
destroy: the user's secrets, the version history and the architecture.

If a rule here conflicts with an explicit instruction from the user, the user
wins — but say out loud which rule you are setting aside and why, so the choice
is visible in the transcript.

## 1. Never touch `.env`

`.env` holds the Anthropic API key and the Telegram bot token.

- Do not open, read, copy, print or summarise it. Nothing in a normal task
  requires its contents.
- Do not create it, edit it or overwrite it. If a task seems to need a
  configuration change, edit `.env.example` and tell the user which line to
  change in their own `.env`.
- Do not remove `.env` from `.gitignore`, and do not `git add -f` it.
- Never paste a real key, token or user ID into code, tests, documentation, a
  commit message or a chat reply. Use the placeholders from `.env.example`.

If you ever see a real secret in a diff, in a log or in the history, stop and
tell the user immediately: it has to be revoked, not merely deleted.

## 2. Do not make unrequested architectural changes

Implement what was asked. Nothing more.

An **architectural change** is anything that alters: the module boundaries
(`adapters/` ↔ `core/`), the `ConversationMemory` or `Tool` interfaces, the
shape of the request/response objects, the storage backend, the set of `.env`
variables, the dependency list, the process model, or the deployment layout.

When you believe a different approach would be better:

1. implement what was asked, exactly as asked;
2. write your proposal in `REVISIONE.md` — the decision as specified, whether
   you agree, the concrete alternative with its implementation sketch, its
   pros and cons, and a verdict (do it now / consider later / not worth it);
3. mention it in one line in your reply, and stop there.

`REVISIONE.md` is advisory. The user reads it and decides. Do not implement an
entry from it unless the user asks for that entry by name.

The single exception: an objective error that stops the project from working —
a broken import, a call that cannot succeed, a unit that systemd refuses. Fix
it, and record what you changed and why at the top of `REVISIONE.md`.

## 3. Back up and commit at the end of every session

Work is only safe once it exists in two places. At the end of a session in
which you changed anything:

1. **Verify.** `ruff format .`, `ruff check .` and `pytest` must all pass. Do
   not commit a red tree.
2. **Snapshot.** Run the local backup, from the project root, on Windows:
   ```powershell
   powershell -ExecutionPolicy Bypass -File .\scripts\backup-dev.ps1
   ```
   It writes a dated zip to `D:\EmmaBackups` (rotation keeps the last 14).
   If the script fails — the drive is missing, for instance — say so plainly
   instead of skipping the step in silence.
3. **Commit.** One commit per coherent change, with a message that says what
   changed and why:
   ```
   add exponential backoff to the Anthropic client

   Three attempts with a doubling delay so a short API outage produces a
   polite reply instead of a crash. Retries are disabled in the SDK to keep
   this layer the only source of retry policy.
   ```
   Not `update`, not `fixes`, not `wip`.
4. **Report.** Tell the user, in one or two lines, what you committed and
   whether the backup ran.

Do not `push` unless the user explicitly asks **and** the version being pushed
is complete and working — not mid-session, not mid-feature. Push only when a
coherent, tested milestone is ready (e.g. a full version bump, a finished
feature). Do not `git tag`, do not rewrite history (`rebase`, `reset --hard`,
`commit --amend`, force-push) unless the user explicitly asks. The update flow
is: development PC → GitHub → `git pull` on the server, and rewritten history
breaks it for the copy that is already deployed.

## 4. Do not delete or move files blindly

Before deleting, renaming or moving anything, check whether something else
depends on it: grep the repository for the name, including `docs/GUIDA.md`,
`README.md` and the `systemd/` units, which reference paths and file names.
Say what you found. When in doubt, ask instead of deleting.

## 5. Keep the documentation in step

The documentation is part of the deliverable, not an afterthought. When a
change touches something a document describes, update the document in the same
commit:

| If you change… | Update… |
| --- | --- |
| a `.env` variable | `.env.example`, `config.py`, `docs/GUIDA.md` (ch. 4), `README.md` |
| a module's behaviour or role | its docstring, `docs/GUIDA.md` (ch. 3) |
| the install or deploy steps | `docs/GUIDA.md` (ch. 1 and 4) |
| the backup scripts or units | `docs/GUIDA.md` (ch. 6), `CLAUDE.md` if the command changed |
| anything user-visible | `CHANGELOG.md`, under `[Unreleased]` |

`docs/GUIDA.pdf` is generated from `docs/GUIDA.md`; if you edit the Markdown
and cannot regenerate the PDF, say so explicitly in your reply so the user can
do it.

## 6. Language

**Everything written in this repository is in English** — code, comments,
docstrings, commit messages, and every document, `docs/GUIDA.md`/`.pdf`,
`REVISIONE.md` and `SESSIONS.md` included. Those three were Italian until
v0.4.0 and were translated on the owner's instruction, so that the repository
reads the same way to everyone who finds it.

Two things are deliberately **not** English, and translating them would be a
regression rather than a tidy-up:

- `prompts/system_prompt.txt`, which is EMMA's personality. She talks to her
  owner in Italian. The file is configuration, not documentation.
- The strings the tools return — `"Registrato come fatto #1."` and the rest.
  They are what the user reads on Telegram, and the tests assert them.

Where a document quotes one of those, keep the quotation in the original and
add an English gloss beside it if the meaning carries the point. A translated
quotation is a false one.

In conversation, speak to the owner in Italian. In the files, write English.

## 7. Privacy check before every push

Before any `git push`, scan every tracked file for personally identifiable
information and infrastructure details. This check is mandatory — no exception,
no shortcut.

What to look for and fix before pushing:

- **Real IP addresses** (private or public) — replace with `<host>` or a generic
  description like "local test machine".
- **Hostnames / machine names** — replace with a generic label.
- **Real personal names** — use a project alias or omit.
- **Real email addresses** — use a placeholder or omit.
- **API keys, tokens, passwords** — these must never appear; if found, treat it
  as an incident: stop, tell the user, revoke the key.
- **Telegram user IDs** or other numeric identifiers tied to a real account.
- **Local file-system paths** that reveal username or machine layout
  (e.g. `C:\Users\<name>\...`).

How to run the check:

```bash
# IP addresses
git grep -En "([0-9]{1,3}\.){3}[0-9]{1,3}" -- .

# Common secret prefixes
git grep -iEn "sk-ant-|gsk_|[0-9]{9,}:[A-Za-z]" -- .

# Local paths and usernames
git grep -in "C:\\Users\\" -- .
```

If any match is found: fix it first, then push. Never push and fix later.

## 8. Ask before moving to the next step

Work in stages, and stop between them. Do not chain several stages together
because they all seem obviously right — the user's judgement is meant to land
between them, not only at the end.

The stages of a piece of work, and what to say at each:

| Stage | Report, then ask |
| --- | --- |
| **Understood** | your reading of the request and the plan — *before writing a line* |
| **Implemented and committed** | what changed, the test and lint result, the diff — ask before committing |
| **Committed** | the commit and its message — ask before pushing |
| **Pushed** | ask before deploying |

There is deliberately no gate between implementing and committing beyond the
one above: if tests fail, fix them — that is not a decision for the user. But
the diff is always shown before the commit, because that is the last moment a
misunderstanding costs nothing.

The **Understood** stage is the one that matters most and the one most easily
skipped. A request that seems obvious is exactly the kind that gets
misread — say what you are about to do before you do it, and let the user stop
you cheaply.

This rule holds whether the user is at the keyboard or reachable only through a
message. Full permissions on the machine — so that nothing blocks where nobody
can click — do not remove the need to ask; they move the asking into the
conversation. The two are different layers, and only the first one is waived.

## 9. Keep the tool list true in all five places

Adding or removing a tool touches more than `main.py`, and one of the places is
easy to forget for a long time. Between v0.1 and v0.3 eight tools were added
while `prompts/system_prompt.txt` still said *"non puoi eseguire comandi"* — an
accurate sentence when the tool list was empty, and then repeated to the model
in 39% of its context on every single turn for months. The user noticed before
any test did, because no test was looking.

When the set of registered tools changes, update all five:

| File | What |
| --- | --- |
| `main.py` | the registration itself |
| `tests/test_main_lifespan.py` | the assertion on the exact set of names |
| `docs/GUIDA.md` | the table of tools in chapter 3 |
| `CHANGELOG.md` | it is user-visible |
| `prompts/system_prompt.txt` | **the one that drifted** |

The test on the exact set is a real guard and has caught every addition so far.
Let it fail and update it deliberately; do not loosen it to a count.

The prompt has no such guard, so it gets two rules instead:

- **Never write the list of tools into the prompt.** Naming them there creates a
  second inventory that will disagree with the first — as it already did, in the
  very sentence forbidding the model to recite tools from memory. The prompt says
  that tools exist and that `list_tools` is how to learn which. Nothing more.
- **Never claim a capability that has no tool behind it.** Writing that she can
  browse the web "on request" when no such tool exists does not make her
  careful, it makes her confident and wrong: she will describe a search she
  cannot perform, and a plausible wrong answer is the one nobody checks. A
  capability she lacks is written as one not yet given, with the offer to
  register it as a development job.

