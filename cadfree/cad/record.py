"""Runtime Workplane recorder — a real feature map without forking CadQuery.

CadQuery is a Python DSL over OCCT, not a history kernel. That is not the
same as impossible. We wrap Workplane (no CadQuery fork) while the user
script runs, fingerprint faces after each modeling op, and stamp a pick-id
into the binary STL attribute bytes. The viewer raycasts those triangles.

This is not SolidWorks topological naming. Face identity is a fingerprint
(geom type + center + area) with a fuzzy match so a trimmed plane keeps its
box, and a new cylinder from fillet() becomes THIS fillet. A messy boolean
can still scramble the map; say so instead of inventing IDs.
"""

from __future__ import annotations

import json
import math
import re
import struct
from pathlib import Path
from typing import Any

from cadfree.cad.features import FEATURE_METHODS, extract_features

LIVE_NOTE = (
    "Not a SolidWorks history kernel — we wrap CadQuery at runtime and stamp "
    "face pick-ids into the STL. Click a fillet in the 3D view to select it. "
    "Fingerprints can scramble after a messy boolean. Reorder/suppress is not OCCT TNaming."
)

_IMPORT = re.compile(
    r"^\s*(?:import\s+cadquery(?:\s+as\s+\w+)?|from\s+cadquery\s+import\s+.+)\s*$"
)


def write_binary_stl(path: Path, triangles: list[dict[str, Any]]) -> None:
    """Binary STL with per-triangle uint16 attribute = pick index (0 = unknown)."""
    path = Path(path)
    buf = bytearray()
    buf.extend(b"Cadfree pick STL".ljust(80, b"\0"))
    buf.extend(struct.pack("<I", len(triangles)))
    for tri in triangles:
        n = tri["n"]
        v0, v1, v2 = tri["v0"], tri["v1"], tri["v2"]
        attr = int(tri.get("attr") or 0) & 0xFFFF
        buf.extend(
            struct.pack(
                "<12fH",
                float(n[0]), float(n[1]), float(n[2]),
                float(v0[0]), float(v0[1]), float(v0[2]),
                float(v1[0]), float(v1[1]), float(v1[2]),
                float(v2[0]), float(v2[1]), float(v2[2]),
                attr,
            )
        )
    path.write_bytes(buf)


def read_stl_pick_ids(data: bytes) -> list[int]:
    if len(data) < 84:
        return []
    n = struct.unpack_from("<I", data, 80)[0]
    ids = []
    off = 84
    for _ in range(n):
        if off + 50 > len(data):
            break
        ids.append(struct.unpack_from("<H", data, off + 48)[0])
        off += 50
    return ids


def read_stl_triangles(data: bytes) -> list[dict[str, Any]]:
    """Binary STL triangles including the uint16 pick-id attribute."""
    if len(data) < 84:
        return []
    n = struct.unpack_from("<I", data, 80)[0]
    tris: list[dict[str, Any]] = []
    off = 84
    for _ in range(n):
        if off + 50 > len(data):
            break
        vals = struct.unpack_from("<12fH", data, off)
        tris.append(
            {
                "n": (vals[0], vals[1], vals[2]),
                "v0": (vals[3], vals[4], vals[5]),
                "v1": (vals[6], vals[7], vals[8]),
                "v2": (vals[9], vals[10], vals[11]),
                "attr": int(vals[12]),
            }
        )
        off += 50
    return tris


def attach_live(features: list[dict[str, Any]], live: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Zip recorded modeling ops onto the AST feature list by kind order."""
    if not live:
        return features
    ops = [op for op in (live.get("ops") or []) if op.get("kind") in FEATURE_METHODS]
    fi = 0
    for op in ops:
        while fi < len(features) and features[fi].get("kind") != op.get("kind"):
            fi += 1
        if fi >= len(features):
            break
        features[fi]["pick_index"] = op.get("pick_index") or 0
        features[fi]["live"] = True
        features[fi]["new_faces"] = op.get("new_faces") or 0
        fi += 1
    return features


def load_live(path: Path | None) -> dict[str, Any] | None:
    if not path or not Path(path).is_file():
        return None
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) and data.get("live") else None


def execute_recorded(source: str, stl_path: Path, live_path: Path | None = None) -> dict[str, Any]:
    """Run the script with a recording Workplane wrapper and export a pick STL."""
    import cadquery as real_cq

    rec = Recorder()
    cq = RecordingModule(real_cq, rec)
    env: dict[str, Any] = {"cq": cq, "cadquery": cq, "__name__": "__cadfree__"}
    exec(compile(_strip_imports(source), "part.py", "exec"), env, env)
    solid = env.get("result") or env.get("part") or env.get("r")
    if solid is None:
        return {"ok": False, "error": "Script must assign the solid to `result` (or `part`)."}

    wrapped = solid if isinstance(solid, RecordingWP) else None
    raw = _unwrap(solid)
    try:
        if hasattr(raw, "val"):
            raw.val()
    except Exception as exc:
        return {"ok": False, "error": f"CadQuery result is not a solid: {exc}"}

    triangles = _tessellate_labeled(wrapped, raw)
    if triangles:
        write_binary_stl(stl_path, triangles)
    else:
        real_cq.exporters.export(raw, str(stl_path), exportType="STL")

    step_path = Path(stl_path).with_suffix(".step")
    try:
        real_cq.exporters.export(raw, str(step_path), exportType="STEP")
    except Exception:
        step_path = None

    live = {
        "live": True,
        "kernel": "cadquery-wrap",
        "not": "SolidWorks TNaming / feature suppress / reorder",
        "note": LIVE_NOTE,
        "ops": rec.ops,
        "pick_count": rec.next_pick - 1,
        "triangles": len(triangles),
    }
    if live_path:
        Path(live_path).write_text(json.dumps(live, indent=2), encoding="utf-8")
    tree = extract_features(source, live=live)
    return {
        "ok": True,
        "error": "",
        "step": str(step_path) if step_path and step_path.is_file() else "",
        "live": live,
        "features": tree.get("features") or [],
    }


def _strip_imports(source: str) -> str:
    out = []
    for line in source.splitlines(True):
        if _IMPORT.match(line.rstrip("\n")):
            out.append("# cadfree injects a recording cadquery wrapper\n")
        else:
            out.append(line)
    return "".join(out)


def _unwrap(obj: Any) -> Any:
    if isinstance(obj, RecordingWP):
        return obj._wp
    if isinstance(obj, RecordingModule):
        return obj._real
    return obj


def _faces(obj: Any) -> list[Any]:
    wp = _unwrap(obj)
    if wp is None:
        return []
    try:
        if hasattr(wp, "faces"):
            return list(wp.faces().vals())
    except Exception:
        return []
    return []


def _sig(face: Any) -> dict[str, Any]:
    center = face.Center()
    return {
        "geom": str(face.geomType()),
        "c": (float(center.x), float(center.y), float(center.z)),
        "area": float(face.Area()),
    }


def _remap(old: list[tuple[dict[str, Any], int]], new_faces: list[Any], pick: int) -> list[tuple[dict[str, Any], int]]:
    unused = list(old)
    mapping: list[tuple[dict[str, Any], int]] = []
    unmatched: list[dict[str, Any]] = []

    def key(s: dict[str, Any]) -> tuple:
        c = s["c"]
        return (s["geom"], round(c[0], 2), round(c[1], 2), round(c[2], 2), round(s["area"], 2))

    for face in new_faces:
        s = _sig(face)
        hit = None
        k = key(s)
        for i, (os, _pick) in enumerate(unused):
            if key(os) == k:
                hit = i
                break
        if hit is not None:
            mapping.append((s, unused.pop(hit)[1]))
        else:
            unmatched.append(s)

    for s in unmatched:
        best_i = None
        best_score = 1e9
        for i, (os, _pick) in enumerate(unused):
            if os["geom"] != s["geom"]:
                continue
            d = math.dist(os["c"], s["c"])
            oa, na = os["area"], s["area"]
            ratio = (min(oa, na) / max(oa, na)) if oa > 0 and na > 0 else 0.0
            score = d + (0.0 if ratio > 0.45 else 25.0)
            if score < best_score:
                best_score = score
                best_i = i
        if best_i is not None and best_score < 18.0:
            mapping.append((s, unused.pop(best_i)[1]))
        else:
            mapping.append((s, pick))
    return mapping


def _normal(a: tuple[float, float, float], b: tuple[float, float, float], c: tuple[float, float, float]) -> tuple[float, float, float]:
    ux, uy, uz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
    vx, vy, vz = c[0] - a[0], c[1] - a[1], c[2] - a[2]
    nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
    length = math.sqrt(nx * nx + ny * ny + nz * nz) or 1.0
    return (nx / length, ny / length, nz / length)


def _vec(v: Any) -> tuple[float, float, float]:
    if hasattr(v, "x"):
        return (float(v.x), float(v.y), float(v.z))
    if hasattr(v, "toTuple"):
        t = v.toTuple()
        return (float(t[0]), float(t[1]), float(t[2]))
    return (float(v[0]), float(v[1]), float(v[2]))


def _tessellate_face(face: Any) -> list[tuple[tuple[float, float, float], ...]]:
    try:
        verts, tris = face.tessellate(0.2)
    except Exception:
        return []
    pts = [_vec(v) for v in verts]
    out = []
    for tri in tris:
        a, b, c = pts[tri[0]], pts[tri[1]], pts[tri[2]]
        out.append((_normal(a, b, c), a, b, c))
    return out


def _lookup_pick(face: Any, labeled: list[tuple[dict[str, Any], int]]) -> int:
    s = _sig(face)
    best = 0
    best_d = 1e9
    for os, pick in labeled:
        if os["geom"] != s["geom"]:
            continue
        d = math.dist(os["c"], s["c"]) + abs(os["area"] - s["area"]) * 0.01
        if d < best_d:
            best_d = d
            best = pick
    return best if best_d < 25.0 else 0


def _tessellate_labeled(wrapped: RecordingWP | None, raw: Any) -> list[dict[str, Any]]:
    faces = _faces(wrapped or raw)
    labeled = list(wrapped._map) if wrapped is not None else []
    triangles: list[dict[str, Any]] = []
    for face in faces:
        pick = _lookup_pick(face, labeled) if labeled else 0
        for n, a, b, c in _tessellate_face(face):
            triangles.append({"n": n, "v0": a, "v1": b, "v2": c, "attr": pick})
    return triangles


class Recorder:
    def __init__(self) -> None:
        self.ops: list[dict[str, Any]] = []
        self.next_pick = 1

    def emit(self, kind: str, labeled: list[tuple[dict[str, Any], int]], pick: int) -> None:
        new_faces = sum(1 for _s, p in labeled if p == pick)
        self.ops.append(
            {
                "kind": kind,
                "pick_index": pick if pick else 0,
                "new_faces": new_faces,
                "faces": len(labeled),
            }
        )


class RecordingModule:
    def __init__(self, real: Any, rec: Recorder) -> None:
        object.__setattr__(self, "_real", real)
        object.__setattr__(self, "_rec", rec)

    def Workplane(self, *args: Any, **kwargs: Any) -> RecordingWP:
        wp = RecordingWP(self._real.Workplane(*args, **kwargs), self._rec, [])
        self._rec.emit("Workplane", [], 0)
        return wp

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)


class RecordingWP:
    def __init__(self, wp: Any, rec: Recorder, labeled: list[tuple[dict[str, Any], int]] | None = None) -> None:
        object.__setattr__(self, "_wp", wp)
        object.__setattr__(self, "_rec", rec)
        object.__setattr__(self, "_map", list(labeled or []))

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._wp, name)
        if not callable(attr):
            return attr

        def wrapped(*args: Any, **kwargs: Any) -> Any:
            parent_maps = [list(self._map)]
            plain_args = []
            for a in args:
                if isinstance(a, RecordingWP):
                    parent_maps.append(list(a._map))
                    plain_args.append(a._wp)
                else:
                    plain_args.append(a)
            plain_kwargs = {k: _unwrap(v) for k, v in kwargs.items()}
            result = attr(*plain_args, **plain_kwargs)
            if not _is_workplane(result):
                return result
            kind = name
            if kind not in FEATURE_METHODS:
                child = RecordingWP(result, self._rec, self._map)
                return child
            pick = 0
            if kind != "Workplane":
                pick = self._rec.next_pick
                self._rec.next_pick += 1
            old = [item for m in parent_maps for item in m]
            labeled = _remap(old, _faces(result), pick)
            self._rec.emit(kind, labeled, pick)
            return RecordingWP(result, self._rec, labeled)

        return wrapped

    def val(self, *args: Any, **kwargs: Any) -> Any:
        return self._wp.val(*args, **kwargs)


def _is_workplane(obj: Any) -> bool:
    if isinstance(obj, RecordingWP):
        return True
    return type(obj).__name__ == "Workplane" and hasattr(obj, "faces")
