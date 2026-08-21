"""Street-typical coil springs — catalog data, not a special latch agent.

Rates, solid heights, and Wahl stress are first-order. Not live stock,
not a fatigue SN curve, not a printed TPU gyroid pretending to be music wire.
"""

from __future__ import annotations

from typing import Any

from cadfree.manufacturing.types import Check, Recommendation

G_MUSIC = 79.3e9
G_STAINLESS = 71.0e9
ALLOW_SHEAR_PA = 800e6  # typical small music-wire order of magnitude, not a coupon
PRICE_NOTE = (
    "Street-typical die / music-wire catalog, not live McMaster inventory. "
    "Confirm rate, solid height, and stock on the vendor page."
)


def _spring(
    *,
    id: str,
    kind: str,
    name: str,
    k_n_per_mm: float,
    free_mm: float,
    solid_mm: float,
    d_mm: float,
    D_mm: float,
    n_active: float,
    f_max_n: float,
    price_usd: float,
    material: str = "music wire",
) -> dict[str, Any]:
    return {
        "id": id,
        "role": "spring",
        "kind": kind,
        "name": name,
        "k_n_per_mm": float(k_n_per_mm),
        "free_mm": float(free_mm),
        "solid_mm": float(solid_mm),
        "d_mm": float(d_mm),
        "D_mm": float(D_mm),
        "n_active": float(n_active),
        "f_max_n": float(f_max_n),
        "price_usd": float(price_usd),
        "material": material,
        "G_pa": G_MUSIC if material == "music wire" else G_STAINLESS,
        "in_stock_claim": False,
        "price_note": PRICE_NOTE,
        "envelope_mm": {"od_mm": D_mm + d_mm, "length_mm": free_mm},
    }


SPRINGS: list[dict[str, Any]] = [
    _spring(
        id="comp_latch_8x32",
        kind="compression",
        name="8 mm OD × 32 mm compression (latch / whoop-scale)",
        k_n_per_mm=0.45,
        free_mm=31.8,
        solid_mm=11.5,
        d_mm=0.8,
        D_mm=8.0,
        n_active=10,
        f_max_n=9.0,
        price_usd=1.2,
    ),
    _spring(
        id="comp_gate_12x50",
        kind="compression",
        name="12 mm OD × 50 mm compression (gate / lid)",
        k_n_per_mm=2.1,
        free_mm=50.0,
        solid_mm=18.0,
        d_mm=1.4,
        D_mm=12.0,
        n_active=8,
        f_max_n=55.0,
        price_usd=2.4,
    ),
    _spring(
        id="comp_die_16x38",
        kind="compression",
        name="16 mm OD die spring (punch / heavy preload)",
        k_n_per_mm=18.0,
        free_mm=38.0,
        solid_mm=22.0,
        d_mm=2.5,
        D_mm=16.0,
        n_active=5,
        f_max_n=280.0,
        price_usd=6.5,
    ),
    _spring(
        id="ext_latch_8x40",
        kind="extension",
        name="8 mm OD × 40 mm extension (return hook)",
        k_n_per_mm=0.85,
        free_mm=40.0,
        solid_mm=40.0,
        d_mm=1.0,
        D_mm=8.0,
        n_active=12,
        f_max_n=18.0,
        price_usd=1.8,
    ),
    _spring(
        id="tors_hinge_090",
        kind="torsion",
        name="90° torsion (hinge / spring-return)",
        k_n_per_mm=0.0,
        free_mm=0.0,
        solid_mm=0.0,
        d_mm=1.6,
        D_mm=12.0,
        n_active=4,
        f_max_n=0.0,
        price_usd=3.2,
    ),
]

# Torsion rate lives in extra — N·m/rad, not N/mm.
SPRINGS[-1]["k_nm_per_rad"] = 0.045
SPRINGS[-1]["max_deg"] = 90.0

SPRING_BY_ID = {s["id"]: s for s in SPRINGS}


def _num(value: Any) -> float | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def spec_from_constraints(constraints: dict[str, Any] | None) -> dict[str, Any]:
    blob = dict(constraints or {})
    survey = dict(blob.get("survey") or {})
    merged = {**survey, **blob}
    return {
        "k_n_per_mm": _num(merged.get("k_n_per_mm")),
        "stroke_mm": _num(merged.get("stroke_mm")),
        "spring_force_n": _num(merged.get("spring_force_n") or merged.get("force_n")),
        "free_mm": _num(merged.get("free_mm")),
        "spring_kind": str(merged.get("spring_kind") or "").strip().lower(),
        "cycles": _num(merged.get("cycles")),
    }


def spec_fields_present(spec: dict[str, Any]) -> bool:
    return any(
        spec.get(key) not in (None, "")
        for key in ("k_n_per_mm", "stroke_mm", "spring_force_n", "spring_kind")
    )


def search_springs(query: str = "", kind: str | None = None, limit: int = 8) -> list[dict[str, Any]]:
    tokens = [t for t in (query or "").lower().replace(",", " ").split() if t]
    want = (kind or "").strip().lower()
    scored: list[tuple[int, dict[str, Any]]] = []
    for item in SPRINGS:
        if want and item["kind"] != want:
            continue
        hay = " ".join([item["id"], item["name"], item["kind"], item["role"], item["material"]]).lower()
        score = sum(3 if tok == item["kind"] else 1 for tok in tokens if tok in hay)
        if not tokens:
            score = 1
        if score:
            scored.append((score, item))
    scored.sort(key=lambda pair: (-pair[0], pair[1]["price_usd"]))
    return [item for _, item in scored[: max(1, int(limit or 8))]]


def spring_spec_overlay(constraints: dict[str, Any] | None) -> dict[str, Any] | None:
    """Catalog class check for a named spring spec — same object as a shop DFM check."""
    spec = spec_from_constraints(constraints)
    if not spec_fields_present(spec):
        return None
    k = spec["k_n_per_mm"]
    stroke = spec["stroke_mm"]
    force = spec["spring_force_n"]
    kind = spec["spring_kind"]
    if k is None and force is not None and stroke not in (None, 0):
        k = force / stroke
    if force is None and k is not None and stroke is not None:
        force = k * stroke

    def _pack(possible: bool, verdict: str, summary: str, checks: list, recs: list, extra: dict | None = None) -> dict[str, Any]:
        data = {
            "possible": possible,
            "verdict": verdict,
            "summary": summary,
            "checks": [c.to_dict() for c in checks],
            "recommendations": [r.to_dict() for r in recs],
            "score": extra or spec,
            "assumptions": [
                "Coil rate + solid height + Wahl shear. Not fatigue SN, not live stock, "
                "not a printed TPU spring. " + PRICE_NOTE
            ],
        }
        return data

    if k is None or stroke is None:
        return _pack(
            False,
            "unknown",
            "ask_survey for spring rate (N/mm) and stroke (mm) — do not guess a coil.",
            [
                Check(
                    "catalog_spring",
                    "Catalog spring",
                    "fail",
                    "Need k_n_per_mm and stroke_mm (force_n can substitute for rate).",
                    details={"missing": ["k_n_per_mm" if k is None else None, "stroke_mm" if stroke is None else None]},
                )
            ],
            [Recommendation("spec", "Survey rate and stroke before designing the latch or suspension.")],
        )

    want_kind = kind if kind in {"compression", "extension", "torsion"} else "compression"
    pool = [s for s in SPRINGS if s["kind"] == want_kind] or list(SPRINGS)
    fits = []
    for s in pool:
        if s["kind"] == "torsion":
            continue
        travel = float(s["free_mm"]) - float(s["solid_mm"])
        if stroke > travel + 0.5:
            continue
        if force is not None and force > float(s["f_max_n"]) + 0.5:
            continue
        if k > float(s["k_n_per_mm"]) * 1.35:
            continue
        fits.append(s)

    if fits:
        chosen = min(fits, key=lambda s: abs(float(s["k_n_per_mm"]) - k))
        sentence = (
            f"A {chosen['name']} (~{chosen['k_n_per_mm']:g} N/mm, solid {chosen['solid_mm']:g} mm, "
            f"~${chosen['price_usd']:g}) can do a {stroke:g} mm stroke"
            + (f" at {force:g} N." if force is not None else ".")
        )
        return _pack(
            True,
            "feasible",
            sentence,
            [
                Check(
                    "catalog_spring",
                    "Catalog spring",
                    "pass",
                    sentence,
                    details={"spring_id": chosen["id"]},
                )
            ],
            [],
            {"possible": True, "spring_id": chosen["id"], "wanted": spec, "for_model": sentence},
        )

    alts = [s for s in SPRINGS if s["kind"] != "torsion" and (float(s["free_mm"]) - float(s["solid_mm"])) >= (stroke or 0)]
    recs = [
        Recommendation(
            "spec",
            f"use a {s['name']} (~{s['k_n_per_mm']:g} N/mm, travel {s['free_mm']-s['solid_mm']:g} mm)",
            to_value=s["id"],
        )
        for s in alts[:3]
    ]
    sentence = (
        f"At {k:g} N/mm over {stroke:g} mm"
        + (f" ({force:g} N)" if force is not None else "")
        + " this catalog has no coil that stays off the solid height. "
        "Shorten the stroke, split into two springs, or buy a die spring."
    )
    return _pack(
        False,
        "needs_spec_change",
        sentence,
        [
            Check(
                "catalog_spring",
                "Catalog spring",
                "fail",
                sentence,
                details={"wanted": spec},
            )
        ],
        recs,
        {"possible": False, "wanted": spec, "for_model": sentence, "alternatives": [r.to_dict() for r in recs]},
    )
