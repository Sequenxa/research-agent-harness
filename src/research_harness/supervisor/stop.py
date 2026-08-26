from __future__ import annotations

from pathlib import Path


def stop_flag_path(state_dir: Path) -> Path:
    return Path(state_dir) / ".stop"


def request_stop(state_dir: Path) -> None:
    path = stop_flag_path(state_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("stop\n", encoding="utf-8")


def clear_stop(state_dir: Path) -> None:
    path = stop_flag_path(state_dir)
    if path.exists():
        path.unlink()


def stop_requested(state_dir: Path) -> bool:
    return stop_flag_path(state_dir).exists()
