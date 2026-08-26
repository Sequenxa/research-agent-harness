from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_fingerprint_file(path: Path) -> dict[str, str]:
    if not path.exists():
        msg = f"Fingerprint file not found: {path}"
        raise FileNotFoundError(msg)
    data: Any = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        msg = f"Fingerprint file must contain a JSON object: {path}"
        raise ValueError(msg)
    return {str(key): str(value) for key, value in data.items()}


def write_fingerprint_file(path: Path, fingerprint: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(fingerprint, indent=2, sort_keys=True) + "\n", encoding="utf-8")
