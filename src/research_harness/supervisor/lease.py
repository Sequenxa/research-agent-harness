from __future__ import annotations

import fcntl
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path


@dataclass(frozen=True)
class LeaseInfo:
    project_id: str
    instance_id: str
    pid: int
    acquired_at: datetime
    expires_at: datetime


class ProjectLease:
    """Exclusive file-based lease — one supervisor instance per project.id."""

    def __init__(self, *, state_dir: Path, project_id: str, ttl_seconds: float = 30.0) -> None:
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.project_id = project_id
        self.ttl_seconds = ttl_seconds
        self.lease_path = self.state_dir / f"{project_id}.lease"

    def acquire(self, instance_id: str) -> bool:
        return self._with_lock(
            lambda existing, now: self._can_take(existing, instance_id, now),
            instance_id,
        )

    def renew(self, instance_id: str) -> bool:
        return self._with_lock(
            lambda existing, now: existing is not None and existing.instance_id == instance_id,
            instance_id,
        )

    def release(self, instance_id: str) -> None:
        if not self.lease_path.exists():
            return
        fd = os.open(self.lease_path, os.O_RDWR)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            existing = self._read_fd(fd)
            if existing is not None and existing.instance_id == instance_id:
                os.ftruncate(fd, 0)
                os.lseek(fd, 0, os.SEEK_SET)
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
            if self.lease_path.exists() and self.lease_path.stat().st_size == 0:
                self.lease_path.unlink()

    def read(self) -> LeaseInfo | None:
        if not self.lease_path.exists():
            return None
        return self._read_path(self.lease_path)

    def is_held_by_other(self, instance_id: str) -> bool:
        info = self.read()
        if info is None:
            return False
        if info.instance_id == instance_id:
            return False
        now = datetime.now(UTC)
        if info.expires_at <= now:
            return False
        return self._pid_alive(info.pid)

    def _with_lock(self, allowed: object, instance_id: str) -> bool:
        self.lease_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.lease_path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            now = datetime.now(UTC)
            existing = self._read_fd(fd)
            if not allowed(existing, now):
                return False
            info = LeaseInfo(
                project_id=self.project_id,
                instance_id=instance_id,
                pid=os.getpid(),
                acquired_at=now,
                expires_at=now + timedelta(seconds=self.ttl_seconds),
            )
            self._write_fd(fd, info)
            return True
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def _can_take(self, existing: LeaseInfo | None, instance_id: str, now: datetime) -> bool:
        if existing is None or self._is_stale(existing, now):
            return True
        return existing.instance_id == instance_id

    @staticmethod
    def _is_stale(info: LeaseInfo, now: datetime) -> bool:
        if info.expires_at <= now:
            return True
        return not ProjectLease._pid_alive(info.pid)

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True

    def _read_fd(self, fd: int) -> LeaseInfo | None:
        os.lseek(fd, 0, os.SEEK_SET)
        raw = os.read(fd, 4096)
        if not raw:
            return None
        return self._parse(raw.decode("utf-8"))

    def _read_path(self, path: Path) -> LeaseInfo | None:
        if not path.exists() or path.stat().st_size == 0:
            return None
        return self._parse(path.read_text(encoding="utf-8"))

    def _write_fd(self, fd: int, info: LeaseInfo) -> None:
        payload = json.dumps(
            {
                "project_id": info.project_id,
                "instance_id": info.instance_id,
                "pid": info.pid,
                "acquired_at": info.acquired_at.isoformat(),
                "expires_at": info.expires_at.isoformat(),
            },
            indent=2,
        )
        os.ftruncate(fd, 0)
        os.lseek(fd, 0, os.SEEK_SET)
        os.write(fd, (payload + "\n").encode("utf-8"))

    @staticmethod
    def _parse(raw: str) -> LeaseInfo:
        data = json.loads(raw)
        owner = str(data.get("instance_id", data.get("owner", "")))
        return LeaseInfo(
            project_id=str(data["project_id"]),
            instance_id=owner,
            pid=int(data["pid"]),
            acquired_at=datetime.fromisoformat(str(data["acquired_at"])),
            expires_at=datetime.fromisoformat(str(data["expires_at"])),
        )
