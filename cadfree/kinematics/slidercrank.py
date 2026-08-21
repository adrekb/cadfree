"""Slider-crank: crank + connecting rod + slider on a line.

CadQuery will not do this. Intersection of a circle with a line, lock-up
when the rod cannot reach the slide.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np


def solve_slider_crank(
    a: np.ndarray,
    line_p: np.ndarray,
    line_u: np.ndarray,
    l1: float,
    l2: float,
    theta_rad: float,
    prev_s: float | None = None,
) -> dict[str, Any]:
    """A=crank pivot. Slider is the line line_p + s * unit(line_u). theta from +x."""
    a = np.asarray(a, dtype=float).reshape(2)
    line_p = np.asarray(line_p, dtype=float).reshape(2)
    u = np.asarray(line_u, dtype=float).reshape(2)
    nu = float(np.linalg.norm(u))
    if nu < 1e-9 or l1 < 1e-9 or l2 < 1e-9:
        return {"ok": False, "locked": True, "reason": "slider-crank degenerates (zero length)"}
    u = u / nu
    b = a + l1 * np.array([math.cos(theta_rad), math.sin(theta_rad)])
    w = line_p - b
    qb = 2.0 * float(np.dot(u, w))
    qc = float(np.dot(w, w)) - l2 * l2
    disc = qb * qb - 4.0 * qc
    if disc < -1e-6:
        return {
            "ok": False,
            "locked": True,
            "B": b.tolist(),
            "reason": "rod cannot reach the slider line — lock-up / cannot assemble",
        }
    disc = max(disc, 0.0)
    root = math.sqrt(disc)
    s1 = (-qb + root) / 2.0
    s2 = (-qb - root) / 2.0
    if prev_s is None:
        s = s1 if abs(s1) <= abs(s2) else s2
    else:
        s = s1 if abs(s1 - prev_s) <= abs(s2 - prev_s) else s2
    c = line_p + s * u
    return {
        "ok": True,
        "locked": False,
        "B": b.tolist(),
        "C": c.tolist(),
        "s": float(s),
        "crank_angle_rad": theta_rad,
        "slide_mm": float(s),
    }
