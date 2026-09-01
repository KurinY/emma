"""The two tools and the one context provider that make facts usable.

Two tools, not three. There is deliberately no `recall`: every active fact is
already in front of the model on each turn, so a tool for reading them back
would answer a question the model can already see the answer to -- and a tool
declaration is paid for on every turn, including the ones where the user only
said "ciao". Measured, the declarations already cost about 537 tokens a turn
(REVISIONE.md, entry 18.2); a third one here would be spent on nothing.

What the model may do is therefore narrow: write a fact when asked, and stop
using one when asked. Deciding by itself what is worth remembering is
explicitly not in scope -- see the module docstring of :mod:`tools.facts.store`
and entry 18.
"""

from __future__ import annotations

import logging
from typing import Any, ClassVar

from tools.facts.store import MAX_FACT_LENGTH, FactStore

logger = logging.getLogger(__name__)

#: Ceiling on the characters of fact text put in front of the model. A second
#: guard behind MAX_ACTIVE_FACTS: that one bounds how many, this bounds how
#: much, so neither a hundred short facts nor a few long ones can quietly make
#: every turn expensive.
MAX_CONTEXT_CHARS = 4000


class RememberFact:
    """Write down something the user has asked to be remembered."""

    name = "remember_fact"
    description = (
        "Record something the user has explicitly asked you to remember, so it "
        "survives beyond the recent conversation. Use it only when they ask -- "
        "'ricorda che', 'segnati che', 'non dimenticare che' -- and never on "
        "your own judgement of what seems important. Write the fact as a short "
        "standalone sentence in the user's own words: it will be read later "
        "with none of this conversation around it, so 'la password del wifi e' "
        "X' works and 'e' X' does not."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "fact": {
                "type": "string",
                "description": (
                    "The fact, as one self-contained sentence. At most "
                    f"{MAX_FACT_LENGTH} characters."
                ),
            }
        },
        "required": ["fact"],
    }

    def __init__(self, store: FactStore) -> None:
        """Bind the tool to the store it writes into."""
        self._store = store

    async def run(self, arguments: dict[str, Any]) -> str:
        """Record the fact, or explain why nothing was recorded."""
        fact = str(arguments.get("fact", ""))
        fact_id, reason = await self._store.remember(fact)

        if fact_id is None:
            logger.info("fact refused: %s", reason)
            return f"Non l'ho registrato: {reason}."
        if reason == "gia' registrato":
            return f"Lo ricordavo gia' (fatto #{fact_id}), non l'ho duplicato."

        logger.info("fact #%d recorded", fact_id)
        return f"Registrato come fatto #{fact_id}. Lo ricordero' anche fra molti giorni."


class ForgetFact:
    """Stop using a fact the user no longer wants remembered."""

    name = "forget_fact"
    description = (
        "Stop using a remembered fact, when the user asks you to forget "
        "something. It needs the fact's number, which is shown beside each fact "
        "in what you already know. If the user describes a fact instead of "
        "numbering it and more than one could match, ask which they mean rather "
        "than guessing -- forgetting the wrong one is not visible to them."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "number": {
                "type": "integer",
                "description": "The number of the fact to forget.",
            }
        },
        "required": ["number"],
    }

    def __init__(self, store: FactStore) -> None:
        """Bind the tool to the store it writes into."""
        self._store = store

    async def run(self, arguments: dict[str, Any]) -> str:
        """Forget the fact, or explain why nothing changed."""
        try:
            fact_id = int(arguments["number"])
        except (KeyError, TypeError, ValueError):
            return "Numero del fatto mancante o non valido: non ho dimenticato nulla."

        if await self._store.forget(fact_id):
            logger.info("fact #%d forgotten", fact_id)
            return f"Non usero' piu' il fatto #{fact_id}."
        return f"Il fatto #{fact_id} non esiste o l'avevo gia' dimenticato."


class FactsContext:
    """Put everything remembered in front of the model, every turn.

    Injected rather than searched. With one user and a bounded set, retrieval
    would add machinery whose only failure mode is fetching the wrong thing --
    and there is nothing to fetch wrongly when everything is present.

    A failure here costs the facts, not the turn: the router logs a broken
    provider and answers without it, which is the right trade. An assistant who
    has forgotten your daughter's name is worse than one who remembers it, and
    far better than one who does not reply.
    """

    def __init__(self, store: FactStore) -> None:
        """Bind the provider to the store it reads."""
        self._store = store

    async def snapshot(self) -> str:
        """Render the active facts, or nothing at all when there are none."""
        facts = await self._store.active()
        if not facts:
            return ""

        lines: list[str] = []
        used = 0
        dropped = 0
        for fact in facts:
            line = f"- #{fact.id}: {fact.text}"
            if used + len(line) > MAX_CONTEXT_CHARS:
                dropped += 1
                continue
            lines.append(line)
            used += len(line)

        if dropped:
            logger.warning(
                "%d fact(s) left out of the context: the set exceeds %d characters",
                dropped,
                MAX_CONTEXT_CHARS,
            )
            lines.append(f"- (altri {dropped} fatti non entrano in questo spazio)")

        return (
            "Cose che l'utente ti ha chiesto di ricordare. Sono vere e attuali, "
            "e valgono piu' della conversazione recente se le due si "
            "contraddicono. Il numero serve solo per dimenticarle: non citarlo "
            "se l'utente non parla di dimenticare.\n" + "\n".join(lines)
        )


def facts_tools(store: FactStore) -> tuple[RememberFact, ForgetFact]:
    """Build the tools around one store.

    Args:
        store: The fact store they share.

    Returns:
        The tools, ready to hand to the router.
    """
    return (RememberFact(store), ForgetFact(store))


__all__ = [
    "MAX_CONTEXT_CHARS",
    "FactsContext",
    "ForgetFact",
    "RememberFact",
    "facts_tools",
]
