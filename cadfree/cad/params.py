from __future__ import annotations

import ast
import re
from typing import Any

_PARAMS_ASSIGN = re.compile(
    r"^(PARAMS\s*=\s*)(\{.*?\n\})",
    re.MULTILINE | re.DOTALL,
)


def extract_params(source: str) -> dict[str, Any]:
    """Read a top-level PARAMS = {...} dict from a CadQuery script."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == "PARAMS":
                try:
                    value = ast.literal_eval(node.value)
                except (ValueError, TypeError):
                    return {}
                if isinstance(value, dict):
                    return value
    return {}


def apply_params(source: str, updates: dict[str, Any]) -> str:
    """Update PARAMS values in-source. Unknown keys are added."""
    current = extract_params(source)
    if not current and "PARAMS" not in source:
        block = "PARAMS = {\n" + "".join(f"    {k!r}: {updates[k]!r},\n" for k in updates) + "}\n\n"
        return block + source
    merged = dict(current)
    merged.update(updates)
    rendered = "PARAMS = {\n" + "".join(
        f"    {k!r}: {merged[k]!r},\n" for k in merged
    ) + "}"
    if _PARAMS_ASSIGN.search(source):
        return _PARAMS_ASSIGN.sub(rendered, source, count=1)
    return rendered + "\n\n" + source


STARTER_BRACKET = '''\
"""L-bracket. Tweak PARAMS below, or let the agent rewrite the geometry."""
import cadquery as cq

PARAMS = {
    "width_mm": 40.0,
    "foot_mm": 50.0,
    "upright_mm": 50.0,
    "thickness_mm": 6.0,
    "hole_d_mm": 5.2,
    "hole_offset_mm": 12.0,
    "fillet_mm": 1.2,
}

p = PARAMS
foot = cq.Workplane("XY").box(p["width_mm"], p["foot_mm"], p["thickness_mm"])
upright = (
    cq.Workplane("XZ")
    .box(p["width_mm"], p["upright_mm"], p["thickness_mm"])
    .translate((0, p["thickness_mm"] / 2.0, p["upright_mm"] / 2.0 + p["thickness_mm"] / 2.0))
)
result = foot.union(upright)
result = (
    result.faces("<Z")
    .workplane()
    .pushPoints([
        (-p["width_mm"] / 4.0, -p["foot_mm"] / 2.0 + p["hole_offset_mm"]),
        (p["width_mm"] / 4.0, -p["foot_mm"] / 2.0 + p["hole_offset_mm"]),
    ])
    .hole(p["hole_d_mm"])
)
'''
