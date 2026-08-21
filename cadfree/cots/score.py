"""First-order 'can this drone exist at that price/speed?' — not a propeller map.

Class cost floors catch the common lie (an $80 cart does not buy a 50 mph 5-inch).
Excess thrust vs a blunt CdA is the physics backup, not CFD.
"""

from __future__ import annotations

import math
from typing import Any

from cadfree.cots.catalog import CLASSES, PRICE_NOTE, class_by_id, resolve_class_id
from cadfree.physics.book import G

MPH = 0.44704
RHO = 1.225

DISCLAIMER = (
    "Class lookup plus a one-line excess-thrust vs drag speed "
    "(v = sqrt(2 F / (ρ Cd A)), F = sqrt(T² − W²)). Not a propeller map, "
    "not Betaflight, not live vendor stock. " + PRICE_NOTE
)


def _num(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _bool(value: Any) -> bool | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"yes", "true", "1", "y"}:
        return True
    if text in {"no", "false", "0", "n"}:
        return False
    return None


def vmax_ms(thrust_to_weight: float, auw_g: float, cd_a_m2: float) -> float:
    """Level-flight speed from leftover thrust after supporting weight."""
    weight_n = (max(auw_g, 1.0) / 1000.0) * G
    thrust_n = max(float(thrust_to_weight), 0.0) * weight_n
    if thrust_n <= weight_n * 1.02:
        return 0.0
    f_fwd = math.sqrt(max(thrust_n * thrust_n - weight_n * weight_n, 0.0))
    return math.sqrt(2.0 * f_fwd / (RHO * max(float(cd_a_m2), 1e-6)))


def ms_to_mph(ms: float) -> float:
    return float(ms) / MPH


def spec_from_constraints(constraints: dict[str, Any] | None, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    blob = dict(constraints or {})
    survey = dict(blob.get("survey") or {})
    merged = {**survey, **blob, **(extra or {})}
    return {
        "speed_mph": _num(merged.get("speed_mph")),
        "budget_usd": _num(merged.get("budget_usd")),
        "range_km": _num(merged.get("range_km")),
        "payload_g": _num(merged.get("payload_g")),
        "flight_min": _num(merged.get("flight_min")),
        "vehicle_kind": resolve_class_id(merged.get("vehicle_kind")) or str(merged.get("vehicle_kind") or ""),
        "printed_frame": _bool(merged.get("printed_frame")),
        "cots_electronics": _bool(merged.get("cots_electronics")),
    }


def class_physics_mph(cls: dict[str, Any], payload_g: float | None) -> float:
    auw = float(cls["auw_g"]) + float(payload_g or 0)
    phys = ms_to_mph(vmax_ms(float(cls["tw_typical"]), auw, float(cls["cd_a_m2"])))
    stated = float(cls["speed_mph"][1])
    return min(phys, stated)


def class_fail_reasons(
    cls: dict[str, Any],
    *,
    speed_mph: float | None,
    budget_usd: float | None,
    range_km: float | None,
    payload_g: float | None,
    flight_min: float | None,
) -> list[str]:
    reasons: list[str] = []
    if budget_usd is not None and budget_usd < float(cls["cost_floor_usd"]):
        reasons.append(
            f"${budget_usd:g} is below the ~${cls['cost_floor_usd']:g} COTS floor for a {cls['label']}"
        )
    cap = class_physics_mph(cls, payload_g)
    if speed_mph is not None and speed_mph > cap + 0.5:
        reasons.append(
            f"a {cls['label']} tops out around {cap:.0f} mph (class band "
            f"{cls['speed_mph'][0]}–{cls['speed_mph'][1]} mph, first-order drag), not {speed_mph:g}"
        )
    if payload_g is not None and payload_g > float(cls["payload_g"]) + 1:
        reasons.append(
            f"{payload_g:g} g payload is above the ~{cls['payload_g']:g} g this class usually lifts"
        )
    if range_km is not None and range_km > float(cls["range_km"][1]) * 1.2:
        reasons.append(
            f"{range_km:g} km is beyond the ~{cls['range_km'][1]:g} km this class typically covers"
        )
    if flight_min is not None and flight_min > float(cls["flight_min"][1]) * 1.25:
        reasons.append(
            f"{flight_min:g} min is beyond the ~{cls['flight_min'][1]:g} min packs this class fly"
        )
    return reasons


def _alt(
    cls: dict[str, Any],
    *,
    change: str,
    speed_mph: float | None = None,
    budget_usd: float | None = None,
) -> dict[str, Any]:
    cap = class_physics_mph(cls, None)
    return {
        "class_id": cls["id"],
        "label": cls["label"],
        "speed_mph": round(speed_mph if speed_mph is not None else cap, 1),
        "cost_usd": cls["cost_typical_usd"],
        "cost_floor_usd": cls["cost_floor_usd"],
        "change": change,
        "for_model": change,
    }


def spec_fields_present(spec: dict[str, Any]) -> bool:
    """Vehicle class check — not a 50 lb bracket (load_lbf / max_mass_g).

    budget_usd or payload_g alone must not hijack a shop DFM check. Speed, range,
    flight time, or vehicle_kind mean the catalog class table applies.
    """
    return any(
        spec.get(key) not in (None, "")
        for key in ("speed_mph", "range_km", "flight_min", "vehicle_kind")
    )


def catalog_spec_overlay(constraints: dict[str, Any] | None) -> dict[str, Any] | None:
    """Same feasibility object as a shop check — classes are catalog rows, not a drone agent."""
    from cadfree.manufacturing.types import Check, Recommendation

    spec = spec_from_constraints(constraints)
    if not spec_fields_present(spec):
        return None
    score = score_vehicle_spec(**spec)

    def _pack(possible: bool, verdict: str, summary: str, checks: list, recs: list) -> dict[str, Any]:
        return {
            "possible": possible,
            "verdict": verdict,
            "summary": summary,
            "checks": [c.to_dict() for c in checks],
            "recommendations": [r.to_dict() for r in recs],
            "score": score,
            "assumptions": [DISCLAIMER],
        }
    if score.get("missing"):
        return _pack(
            False,
            "unknown",
            score["for_model"],
            [
                Check(
                    "catalog_class",
                    "Catalog class",
                    "fail",
                    score["for_model"],
                    details={"missing": score["missing"]},
                )
            ],
            [Recommendation("spec", "Survey speed and budget before designing. Do not invent them.")],
        )
    if score.get("possible") is True:
        return _pack(
            True,
            "feasible",
            score["for_model"],
            [
                Check(
                    "catalog_class",
                    "Catalog class",
                    "pass",
                    score["for_model"],
                    details={"class_id": score.get("class_id")},
                )
            ],
            [],
        )
    recs = [
        Recommendation("spec", str(a.get("change") or a.get("label") or ""), to_value=a.get("class_id"))
        for a in (score.get("alternatives") or [])
    ]
    return _pack(
        False,
        "needs_spec_change",
        score["for_model"],
        [
            Check(
                "catalog_class",
                "Catalog class",
                "fail",
                score["for_model"],
                details={"alternatives": score.get("alternatives") or [], "class_id": score.get("class_id")},
            )
        ],
        recs,
    )


def score_vehicle_spec(
    *,
    speed_mph: float | None = None,
    budget_usd: float | None = None,
    range_km: float | None = None,
    payload_g: float | None = None,
    flight_min: float | None = None,
    vehicle_kind: str | None = None,
    printed_frame: bool | None = None,
    cots_electronics: bool | None = None,
    **_extra: Any,
) -> dict[str, Any]:
    missing: list[str] = []
    if speed_mph is None:
        missing.append("speed_mph")
    if budget_usd is None:
        missing.append("budget_usd")
    wanted = {
        "speed_mph": speed_mph,
        "budget_usd": budget_usd,
        "range_km": range_km,
        "payload_g": payload_g,
        "flight_min": flight_min,
        "vehicle_kind": vehicle_kind or "",
        "printed_frame": printed_frame,
        "cots_electronics": cots_electronics,
    }
    if missing:
        return {
            "ok": True,
            "possible": None,
            "verdict": "need_survey",
            "missing": missing,
            "wanted": wanted,
            "for_model": (
                "ask_survey for the missing numbers — do not guess."
            ),
            "for_user": "Speed and budget first — the form is the next step, not a guessed CAD frame.",
            "disclaimer": DISCLAIMER,
        }

    preferred = class_by_id(vehicle_kind)
    pool = [preferred] if preferred else list(CLASSES)
    fits = [
        cls
        for cls in pool
        if not class_fail_reasons(
            cls,
            speed_mph=speed_mph,
            budget_usd=budget_usd,
            range_km=range_km,
            payload_g=payload_g,
            flight_min=flight_min,
        )
    ]
    if not fits and preferred:
        # User named a class that cannot do the numbers — fall back to any class.
        fits = [
            cls
            for cls in CLASSES
            if not class_fail_reasons(
                cls,
                speed_mph=speed_mph,
                budget_usd=budget_usd,
                range_km=range_km,
                payload_g=payload_g,
                flight_min=flight_min,
            )
        ]

    physics = []
    for cls in CLASSES:
        cap = class_physics_mph(cls, payload_g)
        physics.append(
            {
                "class_id": cls["id"],
                "vmax_mph": round(cap, 1),
                "cost_floor_usd": cls["cost_floor_usd"],
                "reasons": class_fail_reasons(
                    cls,
                    speed_mph=speed_mph,
                    budget_usd=budget_usd,
                    range_km=range_km,
                    payload_g=payload_g,
                    flight_min=flight_min,
                ),
            }
        )

    electronics_note = ""
    if cots_electronics is False:
        electronics_note = (
            " Cadfree does not wind BLDC stators or layout a flight controller. "
            "Electronics stay COTS even if you print the airframe."
        )

    if fits:
        chosen = min(fits, key=lambda c: float(c["cost_typical_usd"]))
        cap = class_physics_mph(chosen, payload_g)
        sentence = (
            f"A {chosen['label']} can do about {speed_mph:g} mph on a ~${chosen['cost_typical_usd']:g} "
            f"COTS cart (floor ~${chosen['cost_floor_usd']:g}). Design the printable airframe "
            f"around those catalog envelopes."
        )
        return {
            "ok": True,
            "possible": True,
            "verdict": "possible",
            "class_id": chosen["id"],
            "class_label": chosen["label"],
            "wanted": wanted,
            "for_model": sentence + electronics_note,
            "for_user": sentence + electronics_note,
            "vmax_mph": round(cap, 1),
            "cost_typical_usd": chosen["cost_typical_usd"],
            "physics": physics,
            "alternatives": [],
            "disclaimer": DISCLAIMER,
        }

    affordable = [c for c in CLASSES if budget_usd >= float(c["cost_floor_usd"])]
    fast_enough = [c for c in CLASSES if speed_mph <= class_physics_mph(c, payload_g) + 0.5]
    suggestions: list[dict[str, Any]] = []
    seen: set[str] = set()
    if affordable:
        keep = max(affordable, key=lambda c: class_physics_mph(c, payload_g))
        cap = class_physics_mph(keep, payload_g)
        suggestions.append(
            _alt(
                keep,
                speed_mph=cap,
                change=(
                    f"keep about ${budget_usd:g} and accept a {keep['label']} "
                    f"at around {cap:.0f} mph"
                ),
            )
        )
        seen.add(keep["id"])
    if fast_enough:
        raise_to = min(fast_enough, key=lambda c: float(c["cost_typical_usd"]))
        if raise_to["id"] not in seen:
            suggestions.append(
                _alt(
                    raise_to,
                    speed_mph=speed_mph,
                    change=(
                        f"raise the budget to about ${raise_to['cost_typical_usd']:g} "
                        f"for a {raise_to['label']} that can do {speed_mph:g} mph"
                    ),
                )
            )

    sentence = (
        f"At ${budget_usd:g} you cannot make a quadcopter that goes {speed_mph:g} mph."
    )
    if suggestions:
        sentence += " We can " + ", or ".join(s["change"] for s in suggestions) + "."
    else:
        sentence += " Lower the speed, raise the budget, or drop payload — this class table has no fit."

    return {
        "ok": True,
        "possible": False,
        "verdict": "impossible",
        "class_id": None,
        "wanted": wanted,
        "for_model": sentence + electronics_note,
        "for_user": sentence + electronics_note,
        "alternatives": suggestions,
        "physics": physics,
        "next": "Quote for_model. Offer alternatives. Do not write CadQuery for the impossible spec.",
        "disclaimer": DISCLAIMER,
    }
