"""Persistent facts: the memory module, installable and removable in one line.

Registered in ``main.py`` by adding its tools and its context provider; removed
by deleting that line. ``core/`` never learns what a fact is, exactly as it
never learned what a development task is -- the ``Tool`` and ``ContextProvider``
protocols are the whole contract.

Named ``facts`` and not ``memory`` on purpose: ``core/memory.py`` already exists
and is a different thing, the conversation history that forgets by age. Two
modules called memory, meaning opposite things about forgetting, is the kind of
name collision this project has already paid for once.
"""

from tools.facts.store import MAX_ACTIVE_FACTS, MAX_FACT_LENGTH, Fact, FactStore
from tools.facts.tools import (
    MAX_CONTEXT_CHARS,
    FactsContext,
    ForgetFact,
    RememberFact,
    facts_tools,
)

__all__ = [
    "MAX_ACTIVE_FACTS",
    "MAX_CONTEXT_CHARS",
    "MAX_FACT_LENGTH",
    "Fact",
    "FactStore",
    "FactsContext",
    "ForgetFact",
    "RememberFact",
    "facts_tools",
]
