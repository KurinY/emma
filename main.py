"""Process entry point.

One process, one event loop, two jobs:

* **uvicorn/FastAPI** owns the loop and exposes a tiny health endpoint on
  loopback, which makes the service observable (``curl`` from the server,
  ``systemctl status``) without opening anything to the network;
* **the Telegram adapter** runs inside that same loop, started and stopped by
  the FastAPI lifespan, so a ``systemctl stop`` shuts polling down cleanly
  instead of killing it mid-update.

Run it with ``python main.py``; in production systemd does exactly that (see
``systemd/emma.service``).
"""

from __future__ import annotations

import contextlib
import logging
import sys
import time
from collections.abc import AsyncIterator, Awaitable, Callable

import uvicorn
from fastapi import FastAPI, Response

from adapters.telegram import TelegramAdapter
from config import Config, ConfigError, load_config
from core import version as version_info
from core.llm import AnthropicLanguageModel, GroqLanguageModel, MissingDependencyError
from core.memory import SqliteConversationMemory
from core.router import Router
from core.tasks import TaskStore
from tools.development import DevelopmentContext, development_tools
from tools.introspection import introspection_tools

logger = logging.getLogger("emma")

#: The health endpoint is bound to loopback only.  Version 1 exposes nothing to
#: the network by design: Telegram is reached outbound, via long polling.
HEALTH_HOST = "127.0.0.1"
HEALTH_PORT = 8000

#: Conversation id the health probe reads.  It never exists, which is the
#: point: the read costs nothing and still travels the exact path every turn
#: depends on -- far cheaper than PRAGMA integrity_check, and a better answer
#: to "can this process still serve a message?" than a file inspection is.
HEALTH_PROBE_CONVERSATION = "__health__"

#: Single-line, machine-greppable and human-readable: level, timestamp, logger
#: and event.  systemd captures stdout, so this is what ``journalctl`` shows.
LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%dT%H:%M:%S%z"


def configure_logging() -> None:
    """Send structured logs to stdout and mute the noisiest libraries."""
    logging.basicConfig(
        level=logging.INFO,
        format=LOG_FORMAT,
        datefmt=LOG_DATE_FORMAT,
        stream=sys.stdout,
        force=True,
    )
    # These log every single HTTP request at INFO, which under long polling
    # means a line every few seconds carrying no information at all.  "httpx2"
    # is the copy of httpx vendored by the anthropic SDK.
    for noisy in ("httpx", "httpx2", "telegram.ext.Updater"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def create_app(config: Config) -> FastAPI:
    """Build the FastAPI application, wiring every component together.

    This is the composition root: it is the only place where concrete classes
    are chosen.  Swapping the in-memory store for an SQLite one, or Telegram
    for another channel, is a change to these few lines and nothing else.

    Args:
        config: The validated configuration.

    Returns:
        The application, ready to be served by uvicorn.
    """
    system_prompt = config.read_system_prompt()
    if config.llm_provider == "groq":
        llm = GroqLanguageModel(api_key=config.groq_api_key, model=config.groq_model)
        active_model = config.groq_model
    else:
        llm = AnthropicLanguageModel(api_key=config.anthropic_api_key, model=config.anthropic_model)
        active_model = config.anthropic_model
    memory = SqliteConversationMemory(
        db_path=config.memory_db_path,
        max_messages=config.max_history_messages,
    )
    # The task queue shares the database file with the history, so that the
    # integrity check, the snapshots and the consistent backup already built
    # around that file cover it too.  See REVISIONE.md, entry 17.
    tasks = TaskStore(db_path=config.memory_db_path)
    router = Router(
        llm=llm,
        memory=memory,
        system_prompt=system_prompt,
        tools=(*development_tools(tasks), *introspection_tools()),
        # The queue's shape goes in front of the model on every turn rather
        # than waiting to be asked for: whether a tool gets called is the
        # model's decision, and it repeated a stale answer four times in ten
        # rather than looking. See REVISIONE.md, entry 17.
        context_providers=(DevelopmentContext(tasks),),
    )
    telegram = TelegramAdapter(
        token=config.telegram_bot_token,
        allowed_user_id=config.telegram_allowed_user_id,
        router=router,
    )

    @contextlib.asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        """Start the Telegram adapter with the server and stop it with it.

        Both halves are written to survive a component misbehaving. A start-up
        that fails partway unwinds exactly what it managed to open, and a
        shutdown step that raises does not take the steps after it with it --
        either would otherwise leave SQLite connections and their write-ahead
        logs behind on a process that is on its way out.
        """
        logger.info(
            "starting emma (provider=%s, model=%s, history=%d messages, db=%s)",
            config.llm_provider,
            active_model,
            config.max_history_messages,
            config.memory_db_path,
        )

        # What has actually been started, with how to stop it. A list rather
        # than an assumption: an unwind must undo what happened, not what the
        # happy path would have done.
        started: list[tuple[str, Callable[[], Awaitable[None]]]] = [
            # The model client exists from construction and is always safe to
            # close; registering it first means it is released last, after
            # everything that might still be using it has stopped.
            ("model client", llm.aclose),
        ]

        async def shut_down() -> None:
            """Stop everything that was started, newest first."""
            for name, stop in reversed(started):
                try:
                    await stop()
                except Exception:  # one stubborn resource must not strand the rest
                    logger.exception("failed to shut down the %s", name)

        try:
            await memory.open()
            started.append(("conversation memory", memory.close))
            # After the memory: it is the one that creates the directory and
            # runs the integrity check on the shared file.
            await tasks.open()
            started.append(("task queue", tasks.close))
            await telegram.start()
            started.append(("telegram adapter", telegram.stop))
        except Exception:
            logger.exception("start-up failed; closing what had already opened")
            await shut_down()
            raise

        try:
            yield
        finally:
            logger.info("shutting down")
            await shut_down()

    app = FastAPI(
        title="EMMA",
        # One source of truth: a version repeated in two files is a version
        # that will disagree with itself.
        version=version_info.VERSION,
        summary="Self-hosted personal assistant - text-only v1",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    started_at = time.monotonic()

    @app.get("/health")
    async def health(response: Response) -> dict[str, object]:
        """Report whether the process can still do its job, and which code it is.

        This used to answer ``"ok"`` unconditionally, which made it useless for
        the one thing it exists for: a health check that cannot report ill
        health is a liveness check wearing the wrong name. It now reads the
        conversation store before answering, and says so when it cannot.

        The status code carries the same verdict as the body, so a checker that
        understands nothing but HTTP still gets the answer right.
        """
        store = "ok"
        try:
            await memory.get_history(HEALTH_PROBE_CONVERSATION)
        except Exception as exc:
            store = f"unavailable: {type(exc).__name__}"
            logger.warning("health probe could not read the conversation store: %s", exc)

        # The process being alive says nothing about whether the bot can still
        # hear.  Long polling can stop on its own while everything else keeps
        # running, and a health check that misses that reports "ok" about a bot
        # that has gone deaf -- the exact symptom seen twice on 31 August.
        listening = telegram.is_listening
        if not listening:
            logger.error("health probe: telegram long polling is not running")

        healthy = store == "ok" and listening
        if not healthy:
            response.status_code = 503

        return {
            "status": "ok" if healthy else "degraded",
            "store": store,
            "telegram": "listening" if listening else "not polling",
            "model": active_model,
            "provider": config.llm_provider,
            # The commit, not just the version: it is the only field that
            # answers "is this server running what the repository says?".
            **version_info.summary(),
            "uptime_seconds": round(time.monotonic() - started_at, 1),
            # Three faults in one evening were noticed by the user before they
            # were noticed here.  A running tally is what makes "it has felt
            # slow lately" a question with an answer.
            **router.stats.summary(),
        }

    return app


def main() -> int:
    """Load the configuration and serve the application.

    Returns:
        The process exit code: ``0`` on a clean shutdown, ``2`` when the
        configuration is unusable.
    """
    configure_logging()
    # Building the application is part of start-up, not of serving, so its
    # failures belong here with the configuration's. Both are the user's to
    # fix, and both get one line rather than a traceback: a wall of stack
    # frames would only bury the sentence that says what to do.
    try:
        config = load_config()
        app = create_app(config)
    except ConfigError as exc:
        logger.error("configuration error: %s", exc)
        return 2
    except MissingDependencyError as exc:
        logger.error("%s", exc)
        return 2

    uvicorn.run(
        app,
        host=HEALTH_HOST,
        port=HEALTH_PORT,
        log_config=None,  # keep the formatting set by configure_logging()
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
