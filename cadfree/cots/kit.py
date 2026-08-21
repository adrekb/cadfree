"""Pick a COTS kit and wrap a printable X-frame around those envelopes."""

from __future__ import annotations

import json
from typing import Any

from cadfree.cad.assembly import (
    get_part,
    list_instances,
    list_parts,
    place_instance,
    save_part_notes,
    save_part_source,
    set_active_part,
    upsert_part,
)
from cadfree.cad.params import STARTER_QUAD, apply_params, extract_params
from cadfree.cots.catalog import (
    PRICE_NOTE,
    class_by_id,
    get_item,
    resolve_class_id,
    search_catalog,
)
from cadfree.cots.score import score_vehicle_spec, spec_from_constraints
from cadfree.paths import project_dir
from cadfree.store.db import db


ROLES = ("motor", "prop", "battery", "fc", "esc")
QTY = {"motor": 4, "prop": 4, "battery": 1, "fc": 1, "esc": 1}


def _constraints(project_id: str) -> dict[str, Any]:
    with db() as conn:
        row = conn.execute("SELECT constraints FROM projects WHERE id = ?", (project_id,)).fetchone()
    return json.loads((row["constraints"] if row else None) or "{}")


def _save_constraints(project_id: str, constraints: dict[str, Any]) -> None:
    with db() as conn:
        conn.execute(
            "UPDATE projects SET constraints = ?, updated_at = datetime('now') WHERE id = ?",
            (json.dumps(constraints), project_id),
        )


def frame_params(cls: dict[str, Any], chosen: dict[str, dict[str, Any]]) -> dict[str, float]:
    motor = chosen.get("motor") or {}
    fc = chosen.get("fc") or {}
    batt = chosen.get("battery") or {}
    mount_m = motor.get("mount") or {}
    mount_f = fc.get("mount") or {}
    env_b = batt.get("envelope_mm") or {}
    return {
        "arm_mm": float(cls.get("arm_mm") or 220),
        "arm_w_mm": float(cls.get("arm_w_mm") or 14),
        "arm_h_mm": float(cls.get("arm_h_mm") or 6),
        "hub_mm": float(cls.get("hub_mm") or 42),
        "hub_plate_mm": 3.0,
        "motor_pcd_mm": float(mount_m.get("pcd_mm") or 16.0),
        "motor_hole_mm": float(mount_m.get("hole_mm") or 3.0),
        "fc_pcd_mm": float(mount_f.get("pcd_mm") or 30.5),
        "fc_hole_mm": float(mount_f.get("hole_mm") or 3.0),
        "batt_l_mm": float(env_b.get("l") or 72),
        "batt_w_mm": float(env_b.get("w") or 36),
        "batt_h_mm": float(env_b.get("h") or 28),
        "standoff_mm": 20.0,
    }


def pick_kit(
    class_id: str,
    *,
    budget_usd: float | None = None,
    motor_id: str | None = None,
    prop_id: str | None = None,
    battery_id: str | None = None,
    fc_id: str | None = None,
    esc_id: str | None = None,
) -> dict[str, Any]:
    cls = class_by_id(class_id)
    if not cls:
        return {"ok": False, "error": f"unknown vehicle class {class_id}"}
    overrides = {
        "motor": get_item(motor_id),
        "prop": get_item(prop_id),
        "battery": get_item(battery_id),
        "fc": get_item(fc_id),
        "esc": get_item(esc_id),
    }
    chosen: dict[str, dict[str, Any]] = {}
    for role in ROLES:
        if overrides.get(role):
            chosen[role] = overrides[role]  # type: ignore[assignment]
            continue
        cands = search_catalog(role=role, class_id=cls["id"], limit=8)
        if not cands:
            continue
        chosen[role] = min(cands, key=lambda i: float(i["price_usd"]))
    lines = []
    total = 0.0
    mass = float(cls["auw_g"]) * 0.15  # printed frame guess, not a weigh-in
    for role, item in chosen.items():
        qty = QTY.get(role, 1)
        line_usd = qty * float(item["price_usd"])
        total += line_usd
        mass += qty * float(item.get("mass_g") or 0)
        lines.append(
            {
                "role": role,
                "id": item["id"],
                "name": item["name"],
                "qty": qty,
                "unit_usd": item["price_usd"],
                "line_usd": round(line_usd, 2),
                "vendor": item.get("vendor") or "",
                "url": item.get("url") or "",
                "envelope_mm": item.get("envelope_mm") or {},
                "mount": item.get("mount") or {},
                "kind": "purchased",
            }
        )
    params = frame_params(cls, chosen)
    return {
        "ok": True,
        "class_id": cls["id"],
        "class_label": cls["label"],
        "lines": lines,
        "chosen": {role: item["id"] for role, item in chosen.items()},
        "total_usd": round(total, 2),
        "mass_g_purchased": round(mass, 1),
        "over_budget": budget_usd is not None and total > float(budget_usd) + 0.5,
        "budget_usd": budget_usd,
        "frame_params": params,
        "price_note": PRICE_NOTE,
        "disclaimer": (
            "Kit is a class-typical cart from the bundled catalog. "
            "Not live stock. The printable frame PARAMS are the hole patterns "
            "and battery tray of these parts."
        ),
    }


def confirm_questions(score: dict[str, Any], kit: dict[str, Any] | None = None) -> dict[str, Any]:
    if score.get("possible") is True:
        label = score.get("class_label") or score.get("class_id") or "this class"
        total = (kit or {}).get("total_usd")
        extra = f" (~${total:g} catalog)" if total is not None else ""
        return {
            "title": "Build this kit?",
            "questions": [
                {
                    "id": "confirm_kit",
                    "prompt": f"Proceed with a {label}{extra}? Cadfree will hole a printed X-frame for the COTS kit.",
                    "type": "bool",
                    "required": True,
                }
            ],
        }
    alts = score.get("alternatives") or []
    options = [str(a.get("class_id") or "") for a in alts if a.get("class_id")]
    help_bits = [str(a.get("change") or a.get("label") or "") for a in alts]
    if "stop" not in options:
        options.append("stop")
    prompt = score.get("for_user") or score.get("for_model") or "This spec does not close."
    return {
        "title": "This spec does not close",
        "questions": [
            {
                "id": "vehicle_kind",
                "prompt": prompt + " Which path?",
                "type": "choice",
                "options": options,
                "help": " ".join(help_bits),
                "required": True,
            }
        ],
    }


def _part_by_name(project_id: str, name: str) -> dict[str, Any] | None:
    for part in list_parts(project_id):
        if (part.get("name") or "") == name:
            return part
    return None


def _instance_by_name(project_id: str, name: str) -> dict[str, Any] | None:
    for inst in list_instances(project_id):
        if (inst.get("name") or "") == name:
            return inst
    return None


def commit_cots_kit(
    project_id: str,
    *,
    class_id: str | None = None,
    motor_id: str | None = None,
    prop_id: str | None = None,
    battery_id: str | None = None,
    fc_id: str | None = None,
    esc_id: str | None = None,
    accept_alternative: bool = False,
    printed_frame: bool | None = None,
) -> dict[str, Any]:
    constraints = _constraints(project_id)
    spec = spec_from_constraints(constraints, {"vehicle_kind": class_id} if class_id else None)
    score = score_vehicle_spec(**spec)
    chosen_class = resolve_class_id(class_id) or resolve_class_id(spec.get("vehicle_kind")) or score.get("class_id")
    if score.get("possible") is False and not accept_alternative:
        return {
            "ok": False,
            "error": score.get("for_model"),
            "score": score,
            "confirm_survey": confirm_questions(score),
            "next": (
                "The requested speed/budget does not close. Quote check_feasibility "
                "summary, ask_survey with confirm_survey, then commit_cots_kit("
                "accept_alternative=true, class_id=...)."
            ),
        }
    if score.get("missing") and not chosen_class:
        return {
            "ok": False,
            "error": score.get("for_model"),
            "score": score,
            "next": "ask_survey for the missing numbers, then check_feasibility.",
        }
    if not chosen_class:
        alts = score.get("alternatives") or []
        chosen_class = alts[0]["class_id"] if alts else ""
    if not chosen_class:
        return {"ok": False, "error": "no vehicle class to commit"}

    kit = pick_kit(
        chosen_class,
        budget_usd=spec.get("budget_usd"),
        motor_id=motor_id,
        prop_id=prop_id,
        battery_id=battery_id,
        fc_id=fc_id,
        esc_id=esc_id,
    )
    if not kit.get("ok"):
        return kit
    cls = class_by_id(chosen_class)
    assert cls is not None
    params = kit["frame_params"]
    print_frame = printed_frame if printed_frame is not None else spec.get("printed_frame")
    if print_frame is None:
        print_frame = True

    frame_source = apply_params(STARTER_QUAD, params)
    default = get_part(project_id, None)
    frame = upsert_part(
        project_id,
        name="printed X-frame",
        source=frame_source,
        part_id=default["id"],
        kind="part",
        material_id="petg",
    )
    save_part_source(project_id, frame["id"], frame_source)
    set_active_part(project_id, frame["id"])
    save_part_notes(
        frame["id"],
        json.dumps(
            {
                "cots": True,
                "role": "frame",
                "class_id": chosen_class,
                "printed": bool(print_frame),
                "note": (
                    "Printable airframe holed for the committed COTS kit."
                    if print_frame
                    else "Envelope frame matching the kit — buy a similar wheelbase if you are not printing."
                ),
            }
        ),
    )

    placed = []
    arm = float(params["arm_mm"])
    z_motor = float(params["arm_h_mm"]) + 2.0
    for line in kit["lines"]:
        pname = f"COTS {line['name']}"
        existing = _part_by_name(project_id, pname)
        part = upsert_part(
            project_id,
            name=pname,
            source="",
            part_id=existing["id"] if existing else None,
            kind="purchased",
        )
        save_part_notes(
            part["id"],
            json.dumps(
                {
                    "cots": True,
                    "catalog_id": line["id"],
                    "role": line["role"],
                    "url": line.get("url") or "",
                    "vendor": line.get("vendor") or "",
                    "unit_usd": line["unit_usd"],
                    "price_note": PRICE_NOTE,
                    "envelope_mm": line.get("envelope_mm") or {},
                    "mount": line.get("mount") or {},
                }
            ),
        )
        iname = f"cots-{line['role']}"
        prev = _instance_by_name(project_id, iname)
        loc: dict[str, float] = {"x": 0, "y": 0, "z": z_motor if line["role"] in {"motor", "prop"} else 8.0}
        pattern: dict[str, Any] = {"kind": "none"}
        if line["role"] in {"motor", "prop"}:
            pattern = {
                "kind": "circular",
                "count": int(line["qty"]),
                "radius": arm / 2.0,
                "start_deg": 45.0,
                "axis": "z",
            }
        elif line["role"] == "battery":
            loc = {"x": 0, "y": 12.0, "z": -float(params["batt_h_mm"]) / 2.0}
        inst = place_instance(
            project_id,
            part["id"],
            name=iname,
            loc=loc,
            pattern=pattern,
            instance_id=prev["id"] if prev else None,
        )
        placed.append({"part": part["id"], "instance": inst["id"], "role": line["role"]})

    set_active_part(project_id, frame["id"])

    kit_out = {
        **kit,
        "frame_part_id": frame["id"],
        "placed": placed,
        "accept_alternative": bool(accept_alternative),
        "score_possible": score.get("possible"),
        "for_model": (
            score.get("for_model")
            if score.get("possible")
            else (
                f"Committed a {cls['label']} around catalog parts because the original spec did not close. "
                + str(score.get("for_model") or "")
            )
        ),
    }
    constraints["cots_kit"] = {
        "class_id": chosen_class,
        "total_usd": kit["total_usd"],
        "chosen": kit["chosen"],
        "frame_params": params,
    }
    constraints["vehicle_kind"] = chosen_class
    _save_constraints(project_id, constraints)
    (project_dir(project_id) / "kit.json").write_text(
        json.dumps(kit_out, indent=2, default=str)[:200000], encoding="utf-8"
    )
    return {
        "ok": True,
        "kit": kit_out,
        "params": extract_params(frame_source),
        "frame_part_id": frame["id"],
        "next": "build_model on the printed X-frame, then check_feasibility. Purchased motors/FC/battery have no CadQuery solid.",
        "disclaimer": kit["disclaimer"],
    }


def search_parts(
    query: str,
    *,
    role: str | None = None,
    class_id: str | None = None,
    budget_usd: float | None = None,
    limit: int = 8,
    include_web: bool = True,
) -> dict[str, Any]:
    catalog = search_catalog(
        query, role=role, class_id=class_id, budget_usd=budget_usd, limit=limit
    )
    vendors: list[dict[str, Any]] = []
    web_note = "Web search skipped."
    if include_web:
        from cadfree.search.standards import search_standards

        blob = " ".join(x for x in (query, role or "", class_id or "") if x)
        web = search_standards(blob or "FPV motor", intent="parts", max_results=int(limit or 8))
        vendors = list(web.get("citations") or web.get("results") or [])
        web_note = web.get("note") or ""
    return {
        "ok": True,
        "query": query,
        "role": role or "",
        "class_id": resolve_class_id(class_id) if class_id else "",
        "catalog": catalog,
        "vendors": vendors,
        "price_note": PRICE_NOTE,
        "note": (
            "Catalog hits are local street-typical parts (not live stock). "
            "Vendor links are search hits — never treat a snippet as in-stock. "
            + web_note
        ),
    }
