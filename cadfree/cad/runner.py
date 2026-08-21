"""Execute CadQuery (or build123d) in a subprocess. Assign `result` (or `part`)."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any

from cadfree.cad.params import extract_params
from cadfree.manufacturing.mesh import load_mesh, metrics_from_mesh

_BUILD123D_IMPORT = re.compile(
    r"^\s*(?:import\s+build123d(?:\s+as\s+\w+)?|from\s+build123d\s+import\s+.+)\s*$",
    re.MULTILINE,
)
_CADQUERY_IMPORT = re.compile(
    r"^\s*(?:import\s+cadquery(?:\s+as\s+\w+)?|from\s+cadquery\s+import\s+.+)\s*$",
    re.MULTILINE,
)

RUNNER = textwrap.dedent(
    r"""
    import json, sys, traceback
    from pathlib import Path

    stl_path = Path(sys.argv[1])
    meta_path = Path(sys.argv[2])
    source = Path(sys.argv[3]).read_text(encoding="utf-8")

    try:
        import cadquery as cq
    except Exception as exc:
        meta_path.write_text(json.dumps({
            "ok": False,
            "error": "CadQuery is not installed in this environment: " + str(exc),
        }), encoding="utf-8")
        sys.exit(2)

    try:
        from cadfree.cad.record import execute_recorded
        live_path = stl_path.with_name("features.live.json")
        recorded = execute_recorded(source, stl_path, live_path)
        if not recorded.get("ok"):
            meta_path.write_text(json.dumps(recorded), encoding="utf-8")
            sys.exit(1)
        meta_path.write_text(json.dumps({
            "ok": True,
            "error": "",
            "step": recorded.get("step") or "",
            "live": recorded.get("live") or {},
        }), encoding="utf-8")
        sys.exit(0)
    except SystemExit:
        raise
    except Exception:
        # Fall through to the unrecorded exporter if the wrapper fails.
        pass

    env = {"cq": cq, "cadquery": cq, "__name__": "__cadfree__"}
    try:
        exec(compile(source, "part.py", "exec"), env, env)
    except Exception:
        meta_path.write_text(json.dumps({
            "ok": False,
            "error": traceback.format_exc()[-4000:],
        }), encoding="utf-8")
        sys.exit(1)

    solid = env.get("result") or env.get("part") or env.get("r")
    if solid is None:
        meta_path.write_text(json.dumps({
            "ok": False,
            "error": "Script must assign the solid to `result` (or `part`).",
        }), encoding="utf-8")
        sys.exit(1)

    try:
        if hasattr(solid, "val"):
            solid.val()
        cq.exporters.export(solid, str(stl_path), exportType="STL")
        step_path = stl_path.with_suffix(".step")
        try:
            cq.exporters.export(solid, str(step_path), exportType="STEP")
        except Exception:
            step_path = None
    except Exception:
        meta_path.write_text(json.dumps({
            "ok": False,
            "error": "Export failed:\\n" + traceback.format_exc()[-4000:],
        }), encoding="utf-8")
        sys.exit(1)

    meta_path.write_text(json.dumps({
        "ok": True,
        "error": "",
        "step": str(step_path) if step_path else "",
    }), encoding="utf-8")
    """
)

BUILD123D_RUNNER = textwrap.dedent(
    r"""
    import json, sys, traceback
    from pathlib import Path

    stl_path = Path(sys.argv[1])
    meta_path = Path(sys.argv[2])
    source = Path(sys.argv[3]).read_text(encoding="utf-8")

    try:
        import build123d  # noqa: F401
    except Exception as exc:
        meta_path.write_text(json.dumps({
            "ok": False,
            "error": (
                "build123d is not installed in this environment: " + str(exc)
                + ". pip install build123d  (same OCP kernel as CadQuery). "
                "Cadfree will not fake a solid."
            ),
            "dialect": "build123d",
        }), encoding="utf-8")
        sys.exit(2)

    env = {"__name__": "__cadfree__"}
    try:
        exec(compile(source, "part.py", "exec"), env, env)
    except Exception:
        meta_path.write_text(json.dumps({
            "ok": False,
            "error": traceback.format_exc()[-4000:],
            "dialect": "build123d",
        }), encoding="utf-8")
        sys.exit(1)

    solid = env.get("result") or env.get("part") or env.get("r")
    if solid is None:
        for val in env.values():
            name = type(val).__name__
            if name in {"Part", "Solid", "Compound", "Shape"}:
                solid = val
                break
            if name == "BuildPart" and hasattr(val, "part"):
                solid = val.part
                break
    if solid is None:
        meta_path.write_text(json.dumps({
            "ok": False,
            "error": "build123d script must assign the solid to `result` (or `part`).",
            "dialect": "build123d",
        }), encoding="utf-8")
        sys.exit(1)

    def _export(obj, dest, kind):
        dest = str(dest)
        errors = []
        try:
            from build123d import export_stl, export_step
            if kind == "stl":
                export_stl(obj, dest)
                return True
            export_step(obj, dest)
            return True
        except Exception as exc:
            errors.append(str(exc))
        method = "export_stl" if kind == "stl" else "export_step"
        if hasattr(obj, method):
            try:
                getattr(obj, method)(dest)
                return True
            except Exception as exc:
                errors.append(str(exc))
        try:
            from OCP.StlAPI import StlAPI_Writer
            from OCP.BRepMesh import BRepMesh_IncrementalMesh
            shape = obj.wrapped if hasattr(obj, "wrapped") else obj
            if kind == "stl":
                BRepMesh_IncrementalMesh(shape, 0.1)
                writer = StlAPI_Writer()
                writer.Write(shape, dest)
                return True
        except Exception as exc:
            errors.append(str(exc))
        raise RuntimeError("; ".join(errors) or "no exporter")

    try:
        _export(solid, stl_path, "stl")
        step_path = stl_path.with_suffix(".step")
        try:
            _export(solid, step_path, "step")
        except Exception:
            step_path = None
    except Exception:
        meta_path.write_text(json.dumps({
            "ok": False,
            "error": "build123d export failed:\\n" + traceback.format_exc()[-4000:],
            "dialect": "build123d",
        }), encoding="utf-8")
        sys.exit(1)

    meta_path.write_text(json.dumps({
        "ok": True,
        "error": "",
        "step": str(step_path) if step_path else "",
        "dialect": "build123d",
        "live": {},
    }), encoding="utf-8")
    """
)


def cadquery_available() -> bool:
    try:
        import cadquery  # noqa: F401
        return True
    except ImportError:
        return False


def build123d_available() -> bool:
    try:
        import build123d  # noqa: F401
        return True
    except ImportError:
        return False


def script_dialect(source: str) -> str:
    """CadQuery is the default. build123d wins only when it is imported and CadQuery is not."""
    has_b = bool(_BUILD123D_IMPORT.search(source or ""))
    has_cq = bool(_CADQUERY_IMPORT.search(source or ""))
    if has_b and not has_cq:
        return "build123d"
    return "cadquery"


def build_cadquery(source: str, workdir: Path, timeout: int = 45) -> dict[str, Any]:
    workdir.mkdir(parents=True, exist_ok=True)
    stl_path = workdir / "model.stl"
    meta_path = workdir / "build.json"
    src_path = workdir / "part.py"
    runner_path = workdir / "_runner.py"
    dialect = script_dialect(source)
    src_path.write_text(source, encoding="utf-8")
    runner_path.write_text(BUILD123D_RUNNER if dialect == "build123d" else RUNNER, encoding="utf-8")
    repo_root = str(Path(__file__).resolve().parent.parent.parent)
    py_path = os.pathsep.join(filter(None, [repo_root, os.environ.get("PYTHONPATH", "")]))
    try:
        completed = subprocess.run(
            [sys.executable, str(runner_path), str(stl_path), str(meta_path), str(src_path)],
            cwd=str(workdir),
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**os.environ, "CADQUERY_LOGLEVEL": "ERROR", "PYTHONPATH": py_path},
            check=False,
        )
    except subprocess.TimeoutExpired:
        engine = "build123d" if dialect == "build123d" else "CadQuery"
        return {
            "ok": False,
            "error": f"{engine} build timed out after {timeout}s",
            "stl_path": None,
            "dialect": dialect,
        }

    meta: dict[str, Any] = {}
    if meta_path.is_file():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            meta = {}
    if not meta.get("ok"):
        err = meta.get("error") or (completed.stderr or completed.stdout or "build failed")[-4000:]
        return {"ok": False, "error": err, "stl_path": None, "dialect": dialect}

    mesh = load_mesh(stl_path)
    metrics = metrics_from_mesh(mesh)
    step = workdir / "model.step"
    return {
        "ok": True,
        "error": "",
        "stl_path": str(stl_path),
        "step_path": str(step) if step.is_file() else None,
        "metrics": metrics.to_dict(),
        "params": extract_params(source),
        "cadquery_available": True,
        "dialect": meta.get("dialect") or dialect,
        "live": meta.get("live") or {},
    }


def cadquery_status() -> dict[str, Any]:
    return {
        "id": "cadquery",
        "label": "CadQuery / OCCT",
        "available": cadquery_available(),
        "install_hint": "pip install cadquery  (needs cadquery-ocp wheels for your platform)",
        "build123d": build123d_status(),
    }


def build123d_status() -> dict[str, Any]:
    return {
        "id": "build123d",
        "label": "build123d / OCP",
        "available": build123d_available(),
        "install_hint": (
            "pip install build123d  (same OCP kernel as CadQuery). "
            "Cadfree runs a build123d script only when it imports build123d and not cadquery."
        ),
    }
