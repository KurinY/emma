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

Do not `push`, do not `git tag`, do not rewrite history (`rebase`, `reset
--hard`, `commit --amend`, force-push) unless the user explicitly asks. The
update flow is: development PC → GitHub → `git pull` on the server, and
rewritten history breaks it for the copy that is already deployed.

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

Code, comments, docstrings, commit messages and the English documents stay in
English. `docs/GUIDA.md`/`.pdf` and `REVISIONE.md` are written in Italian —
keep writing in Italian when you edit them.
