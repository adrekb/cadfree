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
    mu = transmission_angle_deg(b, c, d)
    return {
        "ok": True,
        "locked": False,
        "B": b.tolist(),
        "C": c.tolist(),
        "crank_angle_rad": theta_rad,
        "rocker_angle_rad": math.atan2(c[1] - d[1], c[0] - d[0]),
        "transmission_deg": mu,
    }


def transmission_angle_deg(b: np.ndarray, c: np.ndarray, d: np.ndarray) -> float:
    """Angle between coupler BC and follower CD. 90° is ideal; below ~40° is awkward."""
    b = np.asarray(b, dtype=float)
    c = np.asarray(c, dtype=float)
    d = np.asarray(d, dtype=float)
    v1 = c - b
    v2 = d - c
    n1 = float(np.linalg.norm(v1))
    n2 = float(np.linalg.norm(v2))
    if n1 < 1e-9 or n2 < 1e-9:
        return 0.0
    cos = float(np.clip(np.dot(v1, v2) / (n1 * n2), -1.0, 1.0))
    ang = math.degrees(math.acos(cos))
    return min(ang, 180.0 - ang)
