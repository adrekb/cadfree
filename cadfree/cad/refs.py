"""Stable `@cad[...]` handles over pick-ids, the feature tree, and assembly.

CadQuery is not a history kernel. These are copyable references to what Cadfree
already stamps: STL pick-ids, feature ids (`kind:line:col`), part / instance /
joint rows. Not OCCT TNaming, not a Fusion persistent ID.
"""

from __future__ import annotations

import re
from typing import Any

from cadfree.cad.assembly import get_part, list_instances, list_parts, part_dir
from cadfree.cad.features import extract_features
from cadfree.cad.record import load_live
from cadfree.kinematics.mechanism import list_joints

REF_RE = re.compile(r"@cad\[([a-zA-Z_]+):([^\]]+)\]")
KINDS = ("face", "feature", "part", "instance", "joint")
HONEST = (
    "@cad[kind:id] is a Cadfree handle: face = STL pick-id, feature = script op, "
    "part/instance/joint = assembly rows. Not SolidWorks TNaming."
)


def format_cad_ref(kind: str, ident: Any) -> str:
    kind = (kind or "").strip().lower()
    if kind not in KINDS:
        raise ValueError(f"cad ref kind must be one of {KINDS}")
    return f"@cad[{kind}:{ident}]"


def parse_cad_refs(text: str) -> list[dict[str, str]]:
    out = []
    for match in REF_RE.finditer(text or ""):
        out.append(
            {
                "kind": match.group(1).lower(),
                "id": match.group(2).strip(),
                "raw": match.group(0),
            }
        )
    return out


def annotate_features(features: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for feat in features:
        feat["cad_ref"] = format_cad_ref("feature", feat.get("id") or "")
        pick = int(feat.get("pick_index") or 0)
        feat["face_ref"] = format_cad_ref("face", pick) if pick else None
    return features


def list_cad_refs(project_id: str, part_id: str | None = None) -> dict[str, Any]:
    part = get_part(project_id, part_id)
    live = load_live(part_dir(project_id, part["id"]) / "features.live.json")
    tree = extract_features(part.get("cadquery_source") or "", live=live)
    features = annotate_features(list(tree.get("features") or []))
    refs: list[dict[str, Any]] = []
    for feat in features:
        refs.append(
            {
                "ref": feat["cad_ref"],
                "kind": "feature",
                "id": feat.get("id"),
                "label": feat.get("label") or feat.get("kind"),
                "pick_index": feat.get("pick_index") or 0,
                "face_ref": feat.get("face_ref"),
            }
        )
        if feat.get("face_ref"):
            refs.append(
                {
                    "ref": feat["face_ref"],
                    "kind": "face",
                    "id": str(feat.get("pick_index")),
                    "label": f"pick {feat.get('pick_index')} · {feat.get('kind')}",
                    "feature_id": feat.get("id"),
                }
            )
    for p in list_parts(project_id):
        refs.append(
            {
                "ref": format_cad_ref("part", p["id"]),
                "kind": "part",
                "id": p["id"],
                "label": p.get("name") or p["id"],
            }
        )
    for inst in list_instances(project_id):
        refs.append(
            {
                "ref": format_cad_ref("instance", inst["id"]),
                "kind": "instance",
                "id": inst["id"],
                "label": inst.get("name") or inst["id"],
                "part_id": inst.get("part_id"),
            }
        )
    for joint in list_joints(project_id):
        refs.append(
            {
                "ref": format_cad_ref("joint", joint["id"]),
                "kind": "joint",
                "id": joint["id"],
                "label": joint.get("name") or joint["id"],
            }
        )
    return {
        "ok": True,
        "part_id": part["id"],
        "refs": refs,
        "features": features,
        "honest": HONEST,
        "note": tree.get("note") or HONEST,
    }


def resolve_cad_ref(project_id: str, ref: str, part_id: str | None = None) -> dict[str, Any]:
    parsed = parse_cad_refs(ref.strip() if "@cad[" in (ref or "") else f"@cad[{ref}]")
    if not parsed:
        return {"ok": False, "error": f"not a @cad[...] handle: {ref}", "honest": HONEST}
    item = parsed[0]
    kind, ident = item["kind"], item["id"]
    listed = list_cad_refs(project_id, part_id)
    hit = next((r for r in listed["refs"] if r["kind"] == kind and str(r["id"]) == ident), None)
    if hit is None and kind == "face":
        hit = next((r for r in listed["refs"] if r.get("ref") == item["raw"]), None)
    if hit is None:
        return {
            "ok": False,
            "error": f"no {kind} {ident} on this project. Call list_cad_refs.",
            "honest": HONEST,
        }
    return {"ok": True, "ref": hit["ref"], "resolved": hit, "honest": HONEST}
