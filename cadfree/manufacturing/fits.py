"""ISO 286 fits, print-hole shrink, and worst-case / RSS stackup.

First-order shop tables for 0–120 mm, not the full ISO 286-1:2010 document
and not a CMM report. Printed holes undersize; purchased pins follow the
shaft grade of the named fit. Will this pin go through this hole after
the printer is done?
"""

from __future__ import annotations

import math
from typing import Any

from cadfree.manufacturing.types import Check, Recommendation

# ISO 286-1:2010 IT grades, micrometres. Bands are (over, up_to] millimetres.
IT_BANDS_MM = (
    (0.0, 3.0),
    (3.0, 6.0),
    (6.0, 10.0),
    (10.0, 18.0),
    (18.0, 30.0),
    (30.0, 50.0),
    (50.0, 80.0),
    (80.0, 120.0),
)
IT_UM: dict[int, tuple[int, ...]] = {
    6: (6, 8, 9, 11, 13, 16, 19, 22),
    7: (10, 12, 15, 18, 21, 25, 30, 35),
    8: (14, 18, 22, 27, 33, 39, 46, 54),
    9: (25, 30, 36, 43, 52, 62, 74, 87),
    11: (60, 75, 90, 110, 130, 160, 190, 220),
    12: (100, 120, 150, 180, 210, 250, 300, 350),
    13: (140, 180, 220, 270, 330, 390, 460, 540),
}

# Shaft fundamental deviation (es for f/g/h/p; ei for k), micrometres.
SHAFT_ES_UM = {
    "h": (0, 0, 0, 0, 0, 0, 0, 0),
    "g": (-2, -4, -5, -6, -7, -9, -10, -12),
    "f": (-6, -10, -13, -16, -20, -25, -30, -36),
    "p": (12, 20, 24, 28, 33, 39, 45, 52),
}
# k: small positive ei; es = ei + IT
SHAFT_K_EI_UM = (0, 1, 1, 1, 2, 2, 2, 3)

FITS: dict[str, dict[str, Any]] = {
    "H11/h11": {
        "id": "H11/h11",
        "label": "loose / printed clearance",
        "hole": "H11",
        "shaft": "h11",
        "kind": "clearance",
        "note": "Shop loose fit. Typical starting point for FDM holes + a purchased pin.",
    },
    "H8/h7": {
        "id": "H8/h7",
        "label": "sliding",
        "hole": "H8",
        "shaft": "h7",
        "kind": "clearance",
        "note": "Sliding fit. Needs a reamer or a mill, not an as-printed FDM hole.",
    },
    "H7/g6": {
        "id": "H7/g6",
        "label": "close running",
        "hole": "H7",
        "shaft": "g6",
        "kind": "clearance",
        "note": "Close running. Machine both, or buy a ground pin and ream.",
    },
    "H7/k6": {
        "id": "H7/k6",
        "label": "transition",
        "hole": "H7",
        "shaft": "k6",
        "kind": "transition",
        "note": "Transition. May need a press or may slide — not for printed holes.",
    },
    "H7/p6": {
        "id": "H7/p6",
        "label": "press / interference",
        "hole": "H7",
        "shaft": "p6",
        "kind": "interference",
        "note": "Press fit. Not a 3D-printed hole. Mill or ream the hole.",
    },
}

PROCESS_SHRINK: dict[str, dict[str, Any]] = {
    "fdm": {
        "kind": "undersize",
        "note": "FDM holes print small. First-order 0.15+0.015·d mm, clamped 0.15–0.6. Not your slicer.",
    },
    "sla": {
        "kind": "undersize",
        "note": "SLA/resin holes print slightly small. First-order 0.05+0.005·d mm, clamped 0.04–0.2.",
    },
    "cnc_mill": {"kind": "none", "note": "Milled hole — no print shrink. Reamer still decides the ISO grade."},
    "cnc_router": {"kind": "none", "note": "Routed hole — no print shrink."},
    "mill": {"kind": "none", "note": "Milled hole — no print shrink."},
    "laser_cut": {
        "kind": "oversize",
        "note": "Laser kerf makes sheet holes larger (~0.15 mm first-order), not smaller.",
    },
    "waterjet": {
        "kind": "oversize",
        "note": "Waterjet kerf first-order ~0.5 mm oversize. Taper is not modelled.",
    },
    "metal_am": {
        "kind": "undersize",
        "note": "As-built metal AM holes are rough. First-order 0.2 mm undersize; ream for a fit.",
    },
}

DISCLAIMER = (
    "Compact ISO 286 IT6–IT13 and H/h/g/f/k/p deviations for 0–120 mm. "
    "Not the full standard, not a CMM, not live pin stock. Print shrink is a shop rule of thumb."
)


def _band_index(d_mm: float) -> int:
    d = max(float(d_mm), 0.0)
    for i, (lo, hi) in enumerate(IT_BANDS_MM):
        if d > lo and d <= hi:
            return i
    if d <= 0:
        return 0
    return len(IT_BANDS_MM) - 1


def it_um(grade: int, d_mm: float) -> float:
    row = IT_UM.get(int(grade))
    if not row:
        raise ValueError(f"unsupported IT grade {grade}")
    return float(row[_band_index(d_mm)])


def _parse_grade(token: str) -> tuple[str, int]:
    text = (token or "").strip()
    letter = ""
    digits = ""
    for ch in text:
        if ch.isalpha():
            letter += ch
        elif ch.isdigit():
            digits += ch
    if not letter or not digits:
        raise ValueError(f"bad ISO grade {token!r}")
    return letter, int(digits)


def hole_limits_mm(grade: str, d_mm: float) -> tuple[float, float]:
    """Return (D_min, D_max) millimetres. H holes: EI=0, ES=+IT."""
    letter, it = _parse_grade(grade)
    itv = it_um(it, d_mm) / 1000.0
    if letter.upper() != "H":
        raise ValueError(f"only H holes are tabulated, not {grade}")
    return (float(d_mm), float(d_mm) + itv)


def shaft_limits_mm(grade: str, d_mm: float) -> tuple[float, float]:
    """Return (d_min, d_max) millimetres."""
    letter, it = _parse_grade(grade)
    idx = _band_index(d_mm)
    itv = it_um(it, d_mm) / 1000.0
    key = letter.lower()
    if key == "k":
        ei = SHAFT_K_EI_UM[idx] / 1000.0
        es = ei + itv
        return (float(d_mm) + ei, float(d_mm) + es)
    if key not in SHAFT_ES_UM:
        raise ValueError(f"unsupported shaft grade {grade}")
    es = SHAFT_ES_UM[key][idx] / 1000.0
    ei = es - itv
    lo, hi = float(d_mm) + ei, float(d_mm) + es
    return (min(lo, hi), max(lo, hi))


def shrink_mm(process_kind: str, hole_d_mm: float) -> tuple[float, str]:
    """Positive = hole comes out smaller (undersize). Laser/waterjet are negative (oversize)."""
    kind = (process_kind or "fdm").strip().lower()
    d = max(float(hole_d_mm), 0.0)
    meta = PROCESS_SHRINK.get(kind) or PROCESS_SHRINK["fdm"]
    note = str(meta.get("note") or "")
    how = meta.get("kind")
    if how == "none":
        return 0.0, note
    if kind == "fdm":
        s = min(0.6, max(0.15, 0.15 + 0.015 * d))
        return s, note
    if kind == "sla":
        s = min(0.2, max(0.04, 0.05 + 0.005 * d))
        return s, note
    if kind == "metal_am":
        return 0.2, note
    if kind == "laser_cut":
        return -0.15, note
    if kind == "waterjet":
        return -0.5, note
    return 0.0, note


def evaluate_fit(
    hole_d_mm: float,
    pin_d_mm: float,
    *,
    fit: str = "H11/h11",
    process_kind: str = "fdm",
    shrink_override_mm: float | None = None,
) -> dict[str, Any]:
    name = (fit or "H11/h11").replace(" ", "")
    if name not in FITS:
        known = ", ".join(FITS)
        return {"ok": False, "error": f"unknown fit {fit!r}. Catalog: {known}"}
    row = FITS[name]
    hole_nom = float(hole_d_mm)
    pin_nom = float(pin_d_mm)
    hmin, hmax = hole_limits_mm(row["hole"], hole_nom)
    smin, smax = shaft_limits_mm(row["shaft"], pin_nom)
    if shrink_override_mm is not None:
        shrink = float(shrink_override_mm)
        shrink_note = "shrink_mm from the survey, not the process table."
    else:
        shrink, shrink_note = shrink_mm(process_kind, hole_nom)
    # Printed hole: both limits shift by -shrink (undersize).
    hmin_p, hmax_p = hmin - shrink, hmax - shrink
    c_min = hmin_p - smax
    c_max = hmax_p - smin
    if c_min >= -1e-6:
        kind = "clearance"
    elif c_max <= 1e-6:
        kind = "interference"
    else:
        kind = "transition"
    return {
        "ok": True,
        "fit": row["id"],
        "label": row["label"],
        "catalog_kind": row["kind"],
        "result_kind": kind,
        "hole_d_mm": hole_nom,
        "pin_d_mm": pin_nom,
        "process_kind": process_kind,
        "shrink_mm": shrink,
        "hole_iso_mm": [hmin, hmax],
        "hole_after_process_mm": [hmin_p, hmax_p],
        "shaft_iso_mm": [smin, smax],
        "clearance_min_mm": c_min,
        "clearance_max_mm": c_max,
        "assembles": c_min >= -1e-6 or kind == "interference" and row["kind"] == "interference",
        "note": row["note"],
        "shrink_note": shrink_note,
        "disclaimer": DISCLAIMER,
    }


def stackup_from(
    items: list[dict[str, Any]] | None = None,
    params: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    rows: list[dict[str, Any]] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        try:
            plus = abs(float(item.get("plus_mm") if item.get("plus_mm") is not None else item.get("tol_mm") or 0.0))
            minus = abs(float(item.get("minus_mm") if item.get("minus_mm") is not None else item.get("tol_mm") or 0.0))
            nom = float(item.get("nominal_mm") or 0.0)
        except (TypeError, ValueError):
            continue
        rows.append(
            {
                "name": str(item.get("name") or item.get("id") or f"dim{len(rows)+1}"),
                "nominal_mm": nom,
                "plus_mm": plus,
                "minus_mm": minus,
            }
        )
    p = params or {}
    for key, raw in p.items():
        if not str(key).endswith("_tol_mm"):
            continue
        stem = str(key)[: -len("_tol_mm")]
        try:
            tol = abs(float(raw))
            nom = float(p.get(stem + "_mm") or p.get(stem) or 0.0)
        except (TypeError, ValueError):
            continue
        if any(r["name"] == stem for r in rows):
            continue
        rows.append({"name": stem, "nominal_mm": nom, "plus_mm": tol, "minus_mm": tol})
    if not rows:
        return None
    wc_plus = sum(r["plus_mm"] for r in rows)
    wc_minus = sum(r["minus_mm"] for r in rows)
    wc_band = wc_plus + wc_minus
    rss_band = math.sqrt(sum((r["plus_mm"] + r["minus_mm"]) ** 2 for r in rows))
    nominal = sum(r["nominal_mm"] for r in rows)
    return {
        "ok": True,
        "items": rows,
        "nominal_mm": nominal,
        "wc_plus_mm": wc_plus,
        "wc_minus_mm": wc_minus,
        "wc_band_mm": wc_band,
        "rss_band_mm": rss_band,
        "disclaimer": "Worst-case is the sum of plus and minus. RSS is sqrt(Σ(t_i²)) on the full bands. Not a Monte Carlo, not GD&T.",
    }


def spec_from_constraints(constraints: dict[str, Any] | None, params: dict[str, Any] | None = None) -> dict[str, Any]:
    c = dict(constraints or {})
    survey = dict(c.get("survey") or {})
    p = dict(params or {})
    merged = {**p, **survey, **c}

    def _num(key: str) -> float | None:
        raw = merged.get(key)
        if raw in (None, ""):
            return None
        try:
            return float(raw)
        except (TypeError, ValueError):
            return None

    return {
        "hole_d_mm": _num("hole_d_mm"),
        "pin_d_mm": _num("pin_d_mm"),
        "fit": str(merged.get("fit") or "").strip() or None,
        "process_kind": str(merged.get("process_kind") or "fdm").strip().lower(),
        "shrink_mm": _num("shrink_mm"),
        "stackup_limit_mm": _num("stackup_limit_mm"),
        "stackup": merged.get("stackup") if isinstance(merged.get("stackup"), list) else None,
    }


def spec_fields_present(spec: dict[str, Any], params: dict[str, Any] | None = None) -> bool:
    has_fit = spec.get("hole_d_mm") is not None and spec.get("pin_d_mm") is not None
    has_named = bool(spec.get("fit")) and (spec.get("hole_d_mm") is not None or spec.get("pin_d_mm") is not None)
    has_stack = bool(spec.get("stackup")) or spec.get("stackup_limit_mm") is not None
    has_param_tol = any(str(k).endswith("_tol_mm") for k in (params or {}))
    return bool(has_fit or has_named or has_stack or has_param_tol)


def _pack(possible: bool, verdict: str, summary: str, checks: list, recs: list, extra: dict | None = None) -> dict[str, Any]:
    return {
        "possible": possible,
        "verdict": verdict,
        "summary": summary,
        "checks": [c.to_dict() for c in checks],
        "recommendations": [r.to_dict() for r in recs],
        "score": extra or {},
        "assumptions": [DISCLAIMER],
    }


def fit_spec_overlay(
    constraints: dict[str, Any] | None,
    params: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    spec = spec_from_constraints(constraints, params)
    if not spec_fields_present(spec, params):
        return None

    checks: list[Check] = []
    recs: list[Recommendation] = []
    extra: dict[str, Any] = {"wanted": spec}
    possible = True
    verdict = "feasible"
    sentences: list[str] = []

    hole = spec["hole_d_mm"]
    pin = spec["pin_d_mm"]
    if hole is not None and pin is not None:
        fit_name = spec["fit"] or ("H11/h11" if spec["process_kind"] in {"fdm", "sla"} else "H8/h7")
        report = evaluate_fit(
            hole,
            pin,
            fit=fit_name,
            process_kind=spec["process_kind"] or "fdm",
            shrink_override_mm=spec["shrink_mm"],
        )
        extra["fit"] = report
        if not report.get("ok"):
            return _pack(
                False,
                "unknown",
                report.get("error") or "unknown fit",
                [Check("fit", "ISO fit", "fail", report.get("error") or "unknown fit")],
                [Recommendation("spec", f"Use a catalog fit: {', '.join(FITS)}.")],
            )
        cmin = float(report["clearance_min_mm"])
        cmax = float(report["clearance_max_mm"])
        shrink = float(report["shrink_mm"])
        wanted = report["catalog_kind"]
        got = report["result_kind"]
        sentence = (
            f"{report['fit']} on a {hole:g} mm hole / {pin:g} mm pin "
            f"({spec['process_kind']}, shrink {shrink:+.2f} mm) → "
            f"clearance {cmin:+.3f} to {cmax:+.3f} mm ({got})."
        )
        sentences.append(sentence)
        fail = False
        if wanted == "clearance" and cmin < -1e-4:
            fail = True
            recs.append(
                Recommendation(
                    "geometry",
                    "Enlarge the printed hole (or switch to a mill/ream) so the pin still slides after shrink.",
                    from_value=f"{hole:g} mm hole",
                    to_value=f"hole ≥ {pin + shrink + 0.1:.2f} mm before shrink for a sliding pin",
                )
            )
        elif wanted == "interference" and cmax > 1e-4 and spec["process_kind"] in {"fdm", "sla"}:
            fail = True
            recs.append(
                Recommendation(
                    "process",
                    "A press fit is not an FDM hole. Mill or ream, or keep a clearance fit and glue/set-screw.",
                )
            )
        elif wanted == "clearance" and cmin > 0.4:
            checks.append(
                Check(
                    "fit",
                    "ISO fit",
                    "warn",
                    sentence + " That is a sloppy running fit — pin will rattle.",
                    details=report,
                )
            )
        if fail:
            possible = False
            verdict = "needs_spec_change"
            checks.append(Check("fit", "ISO fit", "fail", sentence, details=report))
        elif not any(c.id == "fit" for c in checks):
            checks.append(Check("fit", "ISO fit", "pass", sentence, details=report))

    stack = stackup_from(spec.get("stackup"), params)
    limit = spec.get("stackup_limit_mm")
    if stack:
        extra["stackup"] = stack
        band = float(stack["wc_band_mm"])
        rss = float(stack["rss_band_mm"])
        msg = (
            f"Stackup worst-case ±{band/2:.3f} mm band {band:.3f} mm "
            f"(RSS {rss:.3f} mm) over {len(stack['items'])} dims."
        )
        sentences.append(msg)
        if limit is not None and band > float(limit) + 1e-9:
            possible = False
            verdict = "needs_spec_change"
            checks.append(
                Check(
                    "stackup",
                    "Tolerance stackup",
                    "fail",
                    msg + f" Exceeds stackup_limit_mm {limit:g}.",
                    details=stack,
                )
            )
            recs.append(
                Recommendation(
                    "spec",
                    "Tighten the plus/minus on the biggest contributors, or raise stackup_limit_mm.",
                )
            )
        else:
            checks.append(Check("stackup", "Tolerance stackup", "pass" if limit is not None else "info", msg, details=stack))

    if not checks:
        return None
    summary = " ".join(sentences) if sentences else "Fit / stackup catalog check."
    extra["for_model"] = summary
    extra["possible"] = possible
    return _pack(possible, verdict, summary, checks, recs, extra)


def catalog_fits() -> list[dict[str, Any]]:
    return [dict(row) for row in FITS.values()]
