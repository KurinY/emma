# EMMA

A self-hosted personal assistant you talk to from Telegram, powered by the
Anthropic API.

> **Status: early stage — personal project, v1: text-only.**
> This is the first working slice of a larger system: one full loop from a
> Telegram message to a model answer and back. It runs, it is tested and it is
> documented, but it has no tools, no persistence and no voice yet. Treat it as
> a foundation to build on rather than a finished product.

## What it does

You write to your own Telegram bot from your phone; a small Python service
running on your own server receives the message over long polling, adds the
recent conversation history, asks Claude for an answer and sends it back. The
bot answers you and nobody else.

What that buys you compared to using a chat app directly:

- **it is yours** — it runs on your hardware, the conversation never touches a
  third-party product, and the only external call is the model API;
- **nothing is exposed** — long polling means no inbound port, no domain, no
  TLS certificate, no webhook to secure;
- **it is a foundation** — the orchestrator is already an agentic loop with
  tool support, so adding real capabilities later does not mean a rewrite.

## Architecture in one picture

```
   Telegram app                Your server                    Anthropic API
   (your phone)          ┌───────────────────────────┐
        │                │  adapters/telegram.py     │
        │  long polling  │        ↕ (request/reply)  │
        └───────────────▶│  core/router.py ──────────┼──────▶  Claude
                         │        ↕                  │◀──────
                         │  core/memory.py           │
                         │  core/llm.py              │
                         └───────────────────────────┘
```

Three ideas hold it together:

1. **Adapter pattern.** `core/` never imports Telegram. The adapter turns an
   update into an `AssistantRequest` (text, user, conversation) and turns the
   `AssistantResponse` back into a chat message. A second channel — a voice
   satellite, a CLI — is a new file in `adapters/`, and nothing else.
2. **Agentic loop from day one.** The router already speaks the Anthropic
   tool-use protocol: call the model, run whatever tools it asks for, feed the
   results back, repeat until it answers. Version 1 registers zero tools, so
   the loop exits immediately — but registering one changes no code in the
   router.
3. **Memory behind an interface.** `ConversationMemory` defines three
   coroutines; version 1 implements them with a dictionary and a sliding
   window. An SQLite implementation drops in without touching the router.

## Requirements

- Python 3.11 or newer
- An [Anthropic API key](https://console.anthropic.com)
- A Telegram bot token from [@BotFather](https://t.me/BotFather) and your own
  numeric user ID (ask [@userinfobot](https://t.me/userinfobot))
- Linux, macOS or Windows to run it; the deployment guide targets Ubuntu Server

## Quick start

```bash
git clone https://github.com/<your-account>/emma.git
cd emma

python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env               # then fill in the three required values
chmod 600 .env

python main.py
```

Now write to your bot from Telegram. You should get an answer within a couple
of seconds, and the log line for it on stdout.

The three values you must fill in are `ANTHROPIC_API_KEY`,
`TELEGRAM_BOT_TOKEN` and `TELEGRAM_ALLOWED_USER_ID`; everything else in
`.env.example` has a sensible default and is documented in place.

For a real deployment — dedicated system user, systemd service, automatic
restart, scheduled backups and restore — follow **`docs/GUIDA.pdf`**, which
takes a freshly installed Ubuntu Server to a running assistant. That guide is
written in Italian; this README is the short version in English.

## Repository layout

```
main.py             process entry point: FastAPI + Telegram in one event loop
config.py           .env loading and validation
adapters/telegram.py the only file that knows Telegram exists
core/router.py      orchestrator: context → model → tool loop → answer
core/llm.py         Anthropic client, retries and backoff
core/memory.py      memory interface + in-memory sliding window
prompts/            the assistant personality, as plain text
scripts/            backup on the server (bash) and on the dev PC (PowerShell)
systemd/            service and backup timer
tests/              pytest suite for the router and the memory
docs/GUIDA.md/.pdf  the full deployment and maintenance manual (Italian)
CLAUDE.md           standing instructions for AI assistants working on this repo
REVISIONE.md        critical review of the design decisions (Italian, advisory)
```

Two documents are in Italian on purpose: `docs/GUIDA.pdf`, the operating manual
for the person running this instance, and `REVISIONE.md`, an internal design
review addressed to the author. Code, comments, docstrings and every other
project document are in English.

## Development

```bash
ruff format .        # formatter
ruff check .         # linter — must pass clean
pytest               # test suite
```

The suite never touches the network: the model is replaced by a scripted fake
that implements the same interface as the real client.

## Roadmap

The phases are deliberately small, and each one lands on the foundation the
previous one left:

- **v0.1 — text (this release).** Telegram, agentic router, in-memory context.
- **v0.2 — persistence.** SQLite behind `ConversationMemory`, so conversations
  survive a restart.
- **v0.3 — skills.** Real tools registered on the router: calendar, notes,
  home automation, web search.
- **v0.4 — voice.** A Raspberry Pi satellite with wake word, speech-to-text and
  text-to-speech, talking to the same core through a new adapter.

## Contributing

Bug reports, questions and pull requests are welcome — see
[CONTRIBUTING.md](CONTRIBUTING.md). One house rule: architectural changes are
discussed in an issue before they are implemented.

## License

MIT — see [LICENSE](LICENSE). Dependencies keep their own licenses.
