"""Atomic JSON read/write for the persisted client-key + snapshot."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def load(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def save(path: Path, data: dict[str, Any]) -> None:
    """Atomic write + chmod 0600. The client-key is bearer-credential-equivalent."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".state.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2, sort_keys=True)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except Exception:
        Path(tmp).unlink(missing_ok=True)
        raise
