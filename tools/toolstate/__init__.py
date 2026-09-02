"""Switching tools off, and removing them in two stages.

Installed with two lines in ``main.py`` like the other modules, and removed by
deleting them. What is new here is that the store also satisfies
:class:`core.router.ToolGate`, which is how a switched-off tool stops being
offered to the model at all.
"""

from tools.toolstate.store import PROTECTED, SwitchedOff, ToolStateStore
from tools.toolstate.tools import EnableTool, RemoveTool, toolstate_tools

__all__ = [
    "PROTECTED",
    "EnableTool",
    "RemoveTool",
    "SwitchedOff",
    "ToolStateStore",
    "toolstate_tools",
]
