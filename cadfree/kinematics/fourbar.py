"""Planar four-bar: the usual 'if I turn this crank, does the rocker move?' check.

CadQuery/OCCT will not do this. Intersection of two circles, Grashof, lock-up.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from cadfree.kinematics.geometry import circle_circle, pick_closer


def link_stats(lengths: list[float]) -> dict[str, Any]:
    vals = [float(v) for v in lengths]
    if len(vals) != 4:
        raise ValueError("need four link lengths: crank, coupler, rocker, ground")
    order = sorted(vals)
    s, p, q, l = order[0], order[1], order[2], order[3]
    grashof = s + l <= p + q + 1e-6
    special = abs((s + l) - (p + q)) <= 1e-6
    names = ("crank", "coupler", "rocker", "ground")
    shortest_is = names[vals.index(s)]
    if not grashof:
        klass = "triple-rocker (cannot fully rotate; will lock in part of the travel)"
    elif special:
        klass = "change-point (s+l = p+q)"
    elif shortest_is == "crank":
        klass = "crank-rocker"
    elif shortest_is == "ground":
        klass = "drag-link (double-crank)"
    elif shortest_is == "coupler":
        klass = "Grashof double-rocker (coupler can fully rotate relative to the others)"
    else:
        klass = "rocker-crank"
    return {
        "lengths": vals,
        "shortest": s,
        "longest": l,
        "shortest_is": shortest_is,
        "grashof": grashof,
        "class": klass,
    }


def solve_fourbar(
    a: np.ndarray,
    d: np.ndarray,
    l1: float,
    l2: float,
    l3: float,
    theta_rad: float,
    prev_c: np.ndarray | None = None,
) -> dict[str, Any]:
    """A=crank pivot, D=rocker pivot. theta is crank angle from +x in the plane."""
    a = np.asarray(a, dtype=float)
    d = np.asarray(d, dtype=float)
    b = a + l1 * np.array([math.cos(theta_rad), math.sin(theta_rad)])
    hits = circle_circle(b, l2, d, l3)
    if not hits:
        return {
            "ok": False,
            "locked": True,
            "B": b.tolist(),
            "reason": "circles miss — linkage is at a lock-up / cannot assemble at this angle",
        }
    c = pick_closer(hits, prev_c)
    return {
        "ok": True,
        "locked": False,
        "B": b.tolist(),
        "C": c.tolist(),
        "crank_angle_rad": theta_rad,
        "rocker_angle_rad": math.atan2(c[1] - d[1], c[0] - d[0]),
    }
