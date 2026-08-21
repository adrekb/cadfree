"""Bring geometry in from Fusion, SolidWorks, FreeCAD, Onshape, Blender, etc.

Native files (.sldprt, .f3d, .ipt) are not readable here. Export STEP (best)
or a mesh (STL / 3MF / OBJ). STEP assemblies split into unique solids whose
world coordinates stay baked in the mesh — the viewer instances them.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import zipfile
from pathlib import Path
from typing import Any

from cadfree.cad.assembly import (
    MAX_UNIQUE_PARTS,
    ensure_default_part,
    get_part,
    list_parts,
    part_dir,
    place_instance,
    save_part_metrics,
    save_part_notes,
    save_part_source,
    upsert_part,
)
from cadfree.cad.params import STARTER_BRACKET
from cadfree.cad.runner import cadquery_available
from cadfree.manufacturing.mesh import load_mesh, metrics_from_mesh
from cadfree.paths import project_dir

MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_IMPORT_SOLIDS = 24
MAX_ZIP_MEMBERS = 24

MESH_EXT = {".stl", ".obj", ".3mf", ".ply", ".gltf", ".glb", ".off"}
BREP_EXT = {".step", ".stp", ".iges", ".igs", ".brep", ".brp"}
ARCHIVE_EXT = {".zip"}
NATIVE_EXT = {
    ".sldprt": "SolidWorks part",
    ".sldasm": "SolidWorks assembly",
    ".f3d": "Fusion 360",
    ".f3z": "Fusion 360 archive",
    ".ipt": "Inventor part",
    ".iam": "Inventor assembly",
    ".x_t": "Parasolid",
    ".x_b": "Parasolid",
    ".prt": "NX/Creo part",
    ".asm": "NX/Creo assembly",
    ".catpart": "CATIA part",
    ".catproduct": "CATIA product",
    ".fcstd": "FreeCAD",
    ".3dxml": "3D XML",
}

STEP_RUNNER = textwrap.dedent(
    r"""
    import json, sys, traceback
    from pathlib import Path

    src = Path(sys.argv[1])
    out_dir = Path(sys.argv[2])
    meta_path = Path(sys.argv[3])
    max_solids = int(sys.argv[4])

    def fail(msg):
        meta_path.write_text(json.dumps({"ok": False, "error": msg}), encoding="utf-8")
        sys.exit(1)

    try:
        import cadquery as cq
    except Exception as exc:
        fail("CadQuery/OCCT is not installed: " + str(exc))

    ext = src.suffix.lower()
    try:
        if ext in {".step", ".stp"}:
            obj = cq.importers.importStep(str(src))
        elif ext in {".iges", ".igs"}:
            importer = getattr(cq.importers, "importIges", None) or getattr(cq.importers, "importIGES", None)
            if importer is None:
                fail("This CadQuery build cannot import IGES. Export STEP or STL from the other program.")
            obj = importer(str(src))
        elif ext in {".brep", ".brp"}:
            from OCP.BRep import BRep_Builder
            from OCP.BRepTools import BRepTools
            from OCP.TopoDS import TopoDS_Shape
            shape = TopoDS_Shape()
            BRepTools.Read_s(shape, str(src), BRep_Builder())
            obj = cq.Workplane().newObject([cq.Shape.cast(shape)])
        else:
            fail("unsupported CAD kernel format " + ext)
    except Exception:
        fail("Import failed:\n" + traceback.format_exc()[-3000:])

    solids = []
    try:
        solids = list(obj.solids().vals())
    except Exception:
        solids = []
    if not solids:
        val = obj.val() if hasattr(obj, "val") else obj
        try:
            solids = list(val.Solids())
        except Exception:
            solids = [val] if val is not None else []
    if not solids:
        fail("No solids in that file.")

    out_dir.mkdir(parents=True, exist_ok=True)
    exported = []
    truncated = max(0, len(solids) - max_solids)
    for i, solid in enumerate(solids[:max_solids]):
        stl = out_dir / f"solid_{i:03d}.stl"
        try:
            cq.exporters.export(solid, str(stl), exportType="STL")
        except Exception:
            try:
                cq.exporters.export(cq.Workplane().newObject([solid]), str(stl), exportType="STL")
            except Exception:
                fail("STL tessellation failed:\n" + traceback.format_exc()[-2000:])
        exported.append(str(stl))

    meta_path.write_text(json.dumps({
        "ok": True,
        "solids": len(solids),
        "exported": exported,
        "truncated": truncated,
    }), encoding="utf-8")
    """
)


def import_status() -> dict[str, Any]:
    cq = cadquery_available()
    return {
        "step_iges_brep": cq,
        "mesh": True,
        "zip": True,
        "install_hint": None
        if cq
        else "pip install cadquery  — needed for STEP/IGES from Fusion/SolidWorks/FreeCAD",
        "formats": {
            "kernel": sorted(BREP_EXT),
            "mesh": sorted(MESH_EXT),
            "archive": sorted(ARCHIVE_EXT),
            "export_these_instead": sorted(NATIVE_EXT.keys()),
        },
    }


def classify(filename: str) -> dict[str, Any]:
    ext = Path(filename or "").suffix.lower()
    if ext in NATIVE_EXT:
        program = NATIVE_EXT[ext]
        return {
            "kind": "native",
            "ext": ext,
            "ok": False,
            "error": (
                f"{program} native files ({ext}) are not readable here. "
                "Export STEP from that program (best for assemblies) or STL/3MF for a single body."
            ),
        }
    if ext in BREP_EXT:
        return {"kind": "kernel", "ext": ext, "ok": True}
    if ext in MESH_EXT:
        return {"kind": "mesh", "ext": ext, "ok": True}
    if ext in ARCHIVE_EXT:
        return {"kind": "zip", "ext": ext, "ok": True}
    return {
        "kind": "unknown",
        "ext": ext,
        "ok": False,
        "error": (
            f"Unsupported file {ext or filename!r}. "
            "Use STEP, IGES, BREP, STL, OBJ, 3MF, PLY, glTF, or a zip of those."
        ),
    }


def _stub(filename: str, fmt: str, extra: str = "") -> str:
    extra = f"\n# {extra}" if extra else ""
    return (
        f"# Imported from {filename} ({fmt}). Geometry is the tessellated mesh.\n"
        "# PARAMS sliders do not apply. Rebuild uses the imported STL.\n"
        "# Edit topology in the original program, then Import CAD again.\n"
        f"{extra}\n"
    )


def _is_starter(part: dict[str, Any]) -> bool:
    src = (part.get("cadquery_source") or "").strip()
    return src == STARTER_BRACKET.strip() and part.get("kind") == "part"


def _starter_to_reuse(project_id: str) -> str | None:
    parts = list_parts(project_id)
    if len(parts) == 1 and _is_starter(parts[0]):
        return parts[0]["id"]
    return None


def _copy_stl(src: Path, dest: Path) -> dict[str, Any]:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if src.resolve() != dest.resolve():
        shutil.copyfile(src, dest)
    mesh = load_mesh(dest)
    return metrics_from_mesh(mesh).to_dict()


def _register_solid(
    project_id: str,
    *,
    name: str,
    stl: Path,
    filename: str,
    fmt: str,
    reuse_id: str | None,
    extra_note: str = "",
) -> dict[str, Any]:
    stub = _stub(filename, fmt, extra_note)
    origin = json.dumps({"imported": True, "filename": filename, "format": fmt})
    try:
        if reuse_id:
            part = upsert_part(
                project_id, name=name, source=stub, part_id=reuse_id, kind="imported"
            )
            save_part_notes(reuse_id, origin)
            part = get_part(project_id, reuse_id)
            placed = False
        else:
            part = upsert_part(project_id, name=name, source=stub, kind="imported")
            save_part_notes(part["id"], origin)
            place_instance(project_id, part["id"], name=name)
            placed = True
    except ValueError as exc:
        raise ValueError(str(exc)) from exc
    metrics = _copy_stl(stl, part_dir(project_id, part["id"]) / "model.stl")
    save_part_metrics(project_id, part["id"], metrics)
    save_part_source(project_id, part["id"], stub)
    return {"part": get_part(project_id, part["id"]), "placed": placed, "metrics": metrics}


def _mesh_file_to_stl(src: Path, dest: Path) -> dict[str, Any]:
    dest.parent.mkdir(parents=True, exist_ok=True)
    mesh = load_mesh(src)
    mesh.export(str(dest), file_type="stl")
    return {"ok": True, "stl": str(dest), "metrics": metrics_from_mesh(mesh).to_dict()}


def _import_kernel(src: Path, work: Path) -> dict[str, Any]:
    if not cadquery_available():
        return {
            "ok": False,
            "error": (
                "STEP/IGES/BREP need CadQuery (Open CASCADE). "
                "pip install cadquery — or export STL/3MF from Fusion, SolidWorks, or FreeCAD."
            ),
        }
    out_dir = work / "solids"
    meta_path = work / "import.json"
    runner = work / "_import_runner.py"
    runner.write_text(STEP_RUNNER, encoding="utf-8")
    try:
        completed = subprocess.run(
            [sys.executable, str(runner), str(src), str(out_dir), str(meta_path), str(MAX_IMPORT_SOLIDS)],
            cwd=str(work),
            capture_output=True,
            text=True,
            timeout=90,
            env={**os.environ, "CADQUERY_LOGLEVEL": "ERROR"},
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "STEP import timed out after 90s"}
    meta: dict[str, Any] = {}
    if meta_path.is_file():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            meta = {}
    if not meta.get("ok"):
        err = meta.get("error") or (completed.stderr or completed.stdout or "import failed")[-4000:]
        return {"ok": False, "error": err}
    return meta


def _import_one_file(
    project_id: str,
    src: Path,
    filename: str,
    *,
    reuse_id: str | None,
) -> dict[str, Any]:
    info = classify(filename)
    if not info.get("ok"):
        return {"ok": False, "error": info.get("error"), "filename": filename}

    room = MAX_UNIQUE_PARTS - len(list_parts(project_id))
    if reuse_id:
        room += 1
    if room <= 0:
        return {
            "ok": False,
            "error": f"Already at {MAX_UNIQUE_PARTS} unique parts. Shop-scale assemblies reuse parts + patterns.",
            "filename": filename,
        }

    created: list[dict[str, Any]] = []
    notes: list[str] = []

    if info["kind"] == "mesh":
        work = project_dir(project_id) / "imports" / "tmp"
        work.mkdir(parents=True, exist_ok=True)
        stl = work / "mesh.stl"
        try:
            _mesh_file_to_stl(src, stl)
        except Exception as exc:
            return {"ok": False, "error": f"Could not read mesh {filename}: {exc}", "filename": filename}
        name = Path(filename).stem or "imported"
        rec = _register_solid(
            project_id,
            name=name,
            stl=stl,
            filename=filename,
            fmt=info["ext"].lstrip("."),
            reuse_id=reuse_id,
            extra_note="Mesh files have no mates. If this was exported in world coordinates it will line up.",
        )
        created.append(rec["part"])
        return {"ok": True, "parts": created, "filename": filename, "notes": notes}

    if info["kind"] == "kernel":
        work = Path(tempfile.mkdtemp(prefix="cadfree-import-"))
        try:
            dest = work / filename
            shutil.copyfile(src, dest)
            meta = _import_kernel(dest, work)
            if not meta.get("ok"):
                return {"ok": False, "error": meta.get("error"), "filename": filename}
            exported = [Path(p) for p in meta.get("exported") or []]
            if meta.get("truncated"):
                notes.append(
                    f"{filename} had {meta.get('solids')} solids; imported the first "
                    f"{len(exported)} (cap {MAX_IMPORT_SOLIDS} unique bodies per file)."
                )
            stem = Path(filename).stem or "imported"
            for i, stl in enumerate(exported[:room]):
                name = stem if len(exported) == 1 else f"{stem}_{i + 1}"
                rec = _register_solid(
                    project_id,
                    name=name,
                    stl=stl,
                    filename=filename,
                    fmt=info["ext"].lstrip("."),
                    reuse_id=reuse_id if i == 0 else None,
                    extra_note="STEP solids keep world coordinates in the mesh.",
                )
                created.append(rec["part"])
            if len(exported) > room:
                notes.append(f"Stopped at {MAX_UNIQUE_PARTS} unique parts; remaining solids skipped.")
            return {
                "ok": True,
                "parts": created,
                "filename": filename,
                "solids": meta.get("solids"),
                "notes": notes,
            }
        finally:
            shutil.rmtree(work, ignore_errors=True)

    return {"ok": False, "error": "internal: unhandled kind", "filename": filename}


def import_bytes(project_id: str, filename: str, data: bytes) -> dict[str, Any]:
    ensure_default_part(project_id)
    if len(data) > MAX_FILE_BYTES:
        return {"ok": False, "error": f"{filename} is larger than 64 MB"}
    info = classify(filename)
    if not info.get("ok"):
        return {"ok": False, "error": info.get("error"), "filename": filename}

    incoming = project_dir(project_id) / "imports"
    incoming.mkdir(parents=True, exist_ok=True)
    saved = incoming / Path(filename).name
    saved.write_bytes(data)

    reuse = _starter_to_reuse(project_id)
    try:
        if info["kind"] == "zip":
            return _import_zip(project_id, saved, filename, reuse_id=reuse)
        return _import_one_file(project_id, saved, filename, reuse_id=reuse)
    except ValueError as exc:
        return {"ok": False, "error": str(exc), "filename": filename}


def _import_zip(
    project_id: str, zip_path: Path, filename: str, *, reuse_id: str | None
) -> dict[str, Any]:
    created: list[dict[str, Any]] = []
    notes: list[str] = []
    errors: list[str] = []
    try:
        with zipfile.ZipFile(zip_path) as zf:
            names = [
                n
                for n in zf.namelist()
                if not n.endswith("/")
                and "__macosx" not in n.lower()
                and not Path(n).name.startswith(".")
            ]
            if len(names) > MAX_ZIP_MEMBERS:
                notes.append(f"Zip has {len(names)} files; reading the first {MAX_ZIP_MEMBERS}.")
                names = names[:MAX_ZIP_MEMBERS]
            with tempfile.TemporaryDirectory(prefix="cadfree-zip-") as tmp:
                for i, name in enumerate(names):
                    raw = zf.read(name)
                    if len(raw) > MAX_FILE_BYTES:
                        errors.append(f"{name}: too large")
                        continue
                    dest = Path(tmp) / Path(name).name
                    dest.write_bytes(raw)
                    try:
                        result = _import_one_file(
                            project_id,
                            dest,
                            Path(name).name,
                            reuse_id=reuse_id if i == 0 and not created else None,
                        )
                    except ValueError as exc:
                        errors.append(f"{name}: {exc}")
                        continue
                    if result.get("ok"):
                        created.extend(result.get("parts") or [])
                        notes.extend(result.get("notes") or [])
                    elif result.get("error"):
                        errors.append(f"{name}: {result['error']}")
    except zipfile.BadZipFile:
        return {"ok": False, "error": f"{filename} is not a valid zip", "filename": filename}
    if not created:
        return {
            "ok": False,
            "error": errors[0] if errors else f"No supported CAD files in {filename}",
            "filename": filename,
            "errors": errors,
        }
    return {
        "ok": True,
        "parts": created,
        "filename": filename,
        "notes": notes,
        "errors": errors,
    }
