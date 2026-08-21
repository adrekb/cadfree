from __future__ import annotations

import os
from pathlib import Path


def data_dir() -> Path:
    raw = os.environ.get("CADFREE_HOME")
    if raw:
        path = Path(raw).expanduser().resolve()
    else:
        path = Path(__file__).resolve().parent.parent / "data"
    path.mkdir(parents=True, exist_ok=True)
    return path


def db_path() -> Path:
    return data_dir() / "cadfree.db"


def project_dir(project_id: str) -> Path:
    path = data_dir() / "projects" / project_id
    path.mkdir(parents=True, exist_ok=True)
    return path
