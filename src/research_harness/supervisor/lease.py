from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path


@dataclass(frozen=True)
class LeaseInfo:
    project_id: str
    owner: str
    pid: int
    acquired_at: datetime
    expires_at: datetime


class ProjectLease:
    """File-based lease — one supervisor per project.id."""

    def __init__(self, *, state_dir: Path, project_id: str, ttl_seconds: float = 30.0) -> None:
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.project_id = project_id
        self.ttl_seconds = ttl_seconds
        self.lease_path = self.state_dir / f"{project_id}.lease"

    def acquire(self, owner: str) -> bool:
        now = datetime.now(UTC)
        existing = self.read()
        if (
            existing is not None
            and existing.expires_at > now
            and existing.owner != owner
            and self._pid_alive(existing.pid)
        ):
            return False
        info = LeaseInfo(
            project_id=self.project_id,
            owner=owner,
            pid=os.getpid(),
            acquired_at=now,
            expires_at=now + timedelta(seconds=self.ttl_seconds),
        )
        self.lease_path.write_text(
            json.dumps(
                {
                    "project_id": info.project_id,
                    "owner": info.owner,
                    "pid": info.pid,
                    "acquired_at": info.acquired_at.isoformat(),
                    "expires_at": info.expires_at.isoformat(),
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        return True

    def release(self, owner: str) -> None:
        existing = self.read()
        if existing is not None and existing.owner == owner and self.lease_path.exists():
            self.lease_path.unlink()

    def read(self) -> LeaseInfo | None:
        if not self.lease_path.exists():
            return None
        data = json.loads(self.lease_path.read_text(encoding="utf-8"))
        return LeaseInfo(
            project_id=str(data["project_id"]),
            owner=str(data["owner"]),
            pid=int(data["pid"]),
            acquired_at=datetime.fromisoformat(str(data["acquired_at"])),
            expires_at=datetime.fromisoformat(str(data["expires_at"])),
        )

    def is_held_by_other(self, owner: str) -> bool:
        info = self.read()
        if info is None:
            return False
        if info.owner == owner:
            return False
        if info.expires_at <= datetime.now(UTC):
            return False
        return self._pid_alive(info.pid)

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True
