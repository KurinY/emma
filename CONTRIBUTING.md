# Contributing to EMMA

Thanks for taking a look. This is a small personal project kept to a
professional standard: readable, tested and documented. Contributions are
welcome as long as they keep it that way.

## Before you write code

**Discuss architecture first, implement second.** This is the one rule that
matters here. If your change adds a dependency, a new module, a new
configuration variable, a storage backend, a channel, or alters how the router,
the memory or the adapters relate to each other, please open an issue and agree
on the approach before opening a pull request. A well-written patch built on an
approach the project does not want is wasted effort, and refusing it is
unpleasant for everybody.

Bug fixes, documentation, tests and small self-contained improvements need no
prior discussion: open the pull request directly.

## Opening an issue

Say which version you are on (`git describe --tags`), what you expected, what
happened, and how to reproduce it. Relevant log lines help a lot:

```bash
journalctl -u emma -n 100 --no-pager
```

**Never paste a log or a configuration file without checking it first.** Your
API key and bot token must not end up in an issue. If one does, revoke it
immediately — at <https://console.anthropic.com> for the Anthropic key, through
[@BotFather](https://t.me/BotFather) for the Telegram token.

## Opening a pull request

1. Fork, then branch from `main` with a descriptive name (`fix/telegram-retry`,
   `docs/deploy-chapter`).
2. Make the change, with tests when it is testable.
3. Run the checks below; they must all pass.
4. Write a commit message that says *what changed and why*, in the imperative:
   `add exponential backoff to the Anthropic client`, not `updates`.
5. Open the pull request against `main`, describing the change and linking the
   issue it addresses.

```bash
ruff format .        # format
ruff check .         # lint — no warnings allowed
pytest               # tests
```

## Code style

The tooling settles most of it: `ruff` is both the formatter and the linter,
configured in `pyproject.toml`, and its verdict is final. The rest is
convention:

- **Language.** Code, comments, docstrings, commit messages and issues in
  English. Two files are Italian on purpose — `docs/GUIDA.md`/`.pdf` and
  `REVISIONE.md`; leave them that way.
- **Docstrings.** Google style, on every public module, class and function.
  Say what it does and why it exists, not what the next line obviously does.
- **Type hints** on every public function.
- **Comments explain decisions**, not mechanics. A comment that repeats the
  code is noise; a comment that records why an alternative was rejected is
  worth its space.
- **Keep `core/` clean.** Nothing under `core/` may import a channel, and no
  channel-specific concept (chat IDs, updates, message formatting) may leak
  into it. That boundary is what makes voice possible later.
- **Configuration comes from `.env`.** No hard-coded paths, user names or
  machine-specific values anywhere — this project has to run on somebody else's
  machine, not only on the author's.

## Tests

`pytest` runs offline and must stay that way: the model is replaced by a
scripted fake implementing the same interface as the real client. If you need a
new kind of fake, put it next to the test that uses it.

The suite does not aim at full coverage; it aims at covering the parts where a
regression would be silent — the router's control flow and the memory window.

## Security

Do not open a public issue for a security problem. Contact the maintainer
directly and give them a chance to fix it first.

## License

By contributing you agree that your contribution is released under the MIT
license, like the rest of the project.
