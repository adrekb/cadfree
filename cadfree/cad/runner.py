"""Execute CadQuery in a subprocess. The script must assign `result` (or `part`)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any

from cadfree.cad.params import extract_params
from cadfree.manufacturing.mesh import load_mesh, metrics_from_mesh

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


def cadquery_available() -> bool:
    try:
        import cadquery  # noqa: F401
        return True
    except Exception:
        return False


def build_cadquery(source: str, workdir: Path, timeout: int = 45) -> dict[str, Any]:
    workdir.mkdir(parents=True, exist_ok=True)
    stl_path = workdir / "model.stl"
    meta_path = workdir / "build.json"
    src_path = workdir / "part.py"
    runner_path = workdir / "_runner.py"
    src_path.write_text(source, encoding="utf-8")
    runner_path.write_text(RUNNER, encoding="utf-8")
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
        return {"ok": False, "error": f"CadQuery build timed out after {timeout}s", "stl_path": None}

    meta: dict[str, Any] = {}
    if meta_path.is_file():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            meta = {}
    if not meta.get("ok"):
        err = meta.get("error") or (completed.stderr or completed.stdout or "build failed")[-4000:]
        return {"ok": False, "error": err, "stl_path": None}

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
        "live": meta.get("live") or {},
    }


def cadquery_status() -> dict[str, Any]:
    return {
        "id": "cadquery",
        "label": "CadQuery / OCCT",
        "available": cadquery_available(),
        "install_hint": "pip install cadquery  (needs cadquery-ocp wheels for your platform)",
    }
