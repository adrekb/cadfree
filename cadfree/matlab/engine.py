"""MATLAB / Octave — same contract as Carrot's academia pack.

Octave is accepted wherever MATLAB is asked for. A machine without either
gets one clear sentence, not a stack trace.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

MATLAB_TIMEOUT = 120
BINARIES = ("matlab", "octave-cli", "octave")


def find_engine() -> dict[str, Any]:
    for name in BINARIES:
        path = shutil.which(name)
        if path:
            engine = "octave" if "octave" in os.path.basename(path).lower() else "matlab"
            return {"available": True, "path": path, "engine": engine, "name": name}
    return {
        "available": False,
        "path": None,
        "engine": None,
        "name": None,
        "install_hint": "Install MATLAB, or GNU Octave (free, close enough for numeric work).",
    }


def _command(binary: str, script_path: str, workdir: str) -> list[str]:
    if os.path.basename(binary).lower().startswith("octave"):
        return [binary, "--no-gui", "--quiet", "--eval", f"cd('{workdir}'); run('{script_path}');"]
    return [binary, "-batch", f"cd('{workdir}'); run('{script_path}');"]


def run_matlab(
    code: str,
    *,
    workdir: Path | None = None,
    timeout: int = MATLAB_TIMEOUT,
) -> dict[str, Any]:
    status = find_engine()
    if not status["available"]:
        return {
            "ok": False,
            "engine": None,
            "stdout": "",
            "stderr": "",
            "artifacts": [],
            "error": "MATLAB/Octave is not installed. " + status["install_hint"],
        }
    persist = workdir is not None
    scratch_ctx = None if persist else tempfile.TemporaryDirectory(prefix="cadfree-matlab-")
    scratch = Path(workdir) if persist else Path(scratch_ctx.name)  # type: ignore[union-attr]
    scratch.mkdir(parents=True, exist_ok=True)
    script = scratch / "cadfree_script.m"
    script.write_text(code, encoding="utf-8")
    try:
        completed = subprocess.run(
            _command(status["path"], str(script), str(scratch)),
            cwd=str(scratch),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        if scratch_ctx:
            scratch_ctx.cleanup()
        return {
            "ok": False,
            "engine": status["engine"],
            "stdout": "",
            "stderr": "",
            "artifacts": [],
            "error": f"script did not finish within {timeout} seconds",
        }

    artifacts = [
        str(scratch / entry)
        for entry in sorted(os.listdir(scratch))
        if entry not in {"cadfree_script.m"} and not entry.startswith(".")
    ]
    result = {
        "ok": completed.returncode == 0,
        "engine": status["engine"],
        "binary": os.path.basename(status["path"]),
        "stdout": (completed.stdout or "").strip()[-12000:],
        "stderr": (completed.stderr or "").strip()[-4000:],
        "artifacts": artifacts,
        "error": "" if completed.returncode == 0 else "the script exited with an error",
    }
    if scratch_ctx:
        scratch_ctx.cleanup()
        result["artifacts"] = [os.path.basename(a) for a in artifacts]
    return result
