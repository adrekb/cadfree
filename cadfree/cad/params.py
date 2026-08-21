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
result = result.edges("|Z").fillet(p["fillet_mm"])
'''


STARTER_QUAD = '''\
"""Printable X-frame. PARAMS match a committed COTS kit — not invented motors.

Hole patterns and the battery tray come from catalog envelopes (motor PCD,
FC square, pack size). Off-the-shelf electronics stay purchased BOM lines.
"""
import cadquery as cq
from math import cos, sin, radians

PARAMS = {
    "arm_mm": 220.0,
    "arm_w_mm": 14.0,
    "arm_h_mm": 6.0,
    "hub_mm": 42.0,
    "hub_plate_mm": 3.0,
    "motor_pcd_mm": 16.0,
    "motor_hole_mm": 3.0,
    "fc_pcd_mm": 30.5,
    "fc_hole_mm": 3.0,
    "batt_l_mm": 72.0,
    "batt_w_mm": 36.0,
    "batt_h_mm": 28.0,
    "standoff_mm": 20.0,
}

p = PARAMS
span = float(p["arm_mm"])
tw = float(p["arm_w_mm"])
th = float(p["arm_h_mm"])
hub = float(p["hub_mm"])
mpcd = float(p["motor_pcd_mm"])
mdia = float(p["motor_hole_mm"])
fcd = float(p["fc_hole_mm"])
fcpcd = float(p["fc_pcd_mm"])
r = span / 2.0
pad_r = max(12.0, mpcd / 2.0 + mdia + 3.0)

cross = (
    cq.Workplane("XY").box(span + 2.0 * pad_r, tw, th)
    .union(cq.Workplane("XY").box(tw, span + 2.0 * pad_r, th))
    .union(cq.Workplane("XY").box(hub, hub, max(th, float(p["hub_plate_mm"]))))
)
pads = (
    cq.Workplane("XY")
    .pushPoints([(r, 0.0), (-r, 0.0), (0.0, r), (0.0, -r)])
    .circle(pad_r)
    .extrude(th)
)
frame = cross.union(pads)

hole_pts = []
for cx, cy in ((r, 0.0), (-r, 0.0), (0.0, r), (0.0, -r)):
    for i in range(4):
        a = radians(45.0 + 90.0 * i)
        hole_pts.append((cx + (mpcd / 2.0) * cos(a), cy + (mpcd / 2.0) * sin(a)))

result = (
    frame.faces(">Z")
    .workplane()
    .pushPoints(hole_pts)
    .hole(mdia)
    .faces(">Z")
    .workplane()
    .rect(fcpcd, fcpcd, forConstruction=True)
    .vertices()
    .hole(fcd)
    .rotate((0, 0, 0), (0, 0, 1), 45)
)
'''
