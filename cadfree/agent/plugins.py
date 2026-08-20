"""DeepSeek Harness-shaped plugins, in Python.

Everything the agent can do is a plugin: tools, skills, the system prompt
section. The loop does not special-case CAD, MATLAB, or DFM — it only runs
whatever is registered. `ask_survey` is the exception: the loop yields the
form to the studio and waits, because a tool handler cannot talk to the UI.
A future `dsh` mount can wrap the same tools.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

ToolHandler = Callable[..., Any]


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: ToolHandler
    mutating: bool = False


@dataclass
class Plugin:
    name: str
    tools: list[Tool] = field(default_factory=list)
    prompt: str = ""
    skills: list[dict[str, str]] = field(default_factory=list)


_PLUGINS: dict[str, Plugin] = {}


def register(plugin: Plugin) -> None:
    _PLUGINS[plugin.name] = plugin


def registered() -> list[Plugin]:
    return list(_PLUGINS.values())


def all_tools() -> dict[str, Tool]:
    out: dict[str, Tool] = {}
    for plugin in _PLUGINS.values():
        for tool in plugin.tools:
            out[tool.name] = tool
    return out


def openai_tools() -> list[dict[str, Any]]:
    specs = []
    for tool in all_tools().values():
        specs.append(
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                },
            }
        )
    return specs


def system_prompt_sections() -> str:
    chunks = [p.prompt.strip() for p in _PLUGINS.values() if p.prompt.strip()]
    return "\n\n".join(chunks)
