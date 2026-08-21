"""Mesh-size convergence helpers. Not a solver — arithmetic on solutions we already have.

Richardson extrapolation on a geometric refinement ratio is the same check an ME
does before trusting a single CalculiX number. Two levels give a relative change;
three levels estimate the order and an extrapolated value. We never invent a
von Mises to fill a missing level.
"""

from __future__ import annotations

import math
from typing import Any


def char_lengths(
    bbox_m: list[float] | tuple[float, ...] | None,
    *,
    n_levels: int = 1,
    base: float | None = None,
    ratio: float = 1.5,
) -> list[float]:
    """Coarse → fine characteristic lengths (metres)."""
    sides = [float(x) for x in (bbox_m or []) if float(x) > 0]
    char = float(base) if base else max((min(sides) / 6.0 if sides else 0.004), 0.0008)
    n = max(1, int(n_levels))
    if n == 1:
        return [char]
    r = max(float(ratio), 1.05)
    return [char / (r**i) for i in range(n)]


def richardson(
    values: list[float],
    *,
    ratio: float = 1.5,
    quantity: str = "value",
) -> dict[str, Any]:
    """values[0] is coarsest. ratio = h_i / h_{i+1} > 1."""
    clean = [float(v) for v in values if v is not None and math.isfinite(float(v))]
    r = max(float(ratio), 1.05)
    out: dict[str, Any] = {
        "quantity": quantity,
        "n": len(clean),
        "values": clean,
        "ratio": r,
        "ok": False,
        "disclaimer": (
            "Richardson on this mesh family only. Not a NAFEMS certificate, "
            "not a proof the BCs are right."
        ),
    }
    if len(clean) < 2:
        out["error"] = "Need at least two mesh levels to report a change."
        return out
    f_coarse, f_fine = clean[-2], clean[-1]
    rel = abs(f_fine - f_coarse) / max(abs(f_fine), 1e-30)
    out["relative_change"] = rel
    out["finest"] = f_fine
    out["ok"] = True
    if len(clean) >= 3:
        f1, f2, f3 = clean[-3], clean[-2], clean[-1]
        num = f2 - f1
        den = f3 - f2
        if abs(den) > 1e-30 and (num / den) > 0:
            p = math.log(num / den) / math.log(r)
            out["observed_order"] = p
            denom = r**p - 1.0
            if abs(denom) > 1e-12:
                out["extrapolated"] = f3 + (f3 - f2) / denom
        else:
            out["observed_order"] = None
            out["note"] = "Not monotonic — will not extrapolate. Report the finest mesh."
    out["converged"] = rel <= 0.05
    out["band"] = (
        f"{quantity} = {f_fine:.4g} ± {100.0 * rel:.1f}% between the last two meshes"
        if math.isfinite(rel)
        else None
    )
    return out
