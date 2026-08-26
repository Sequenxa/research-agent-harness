from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any
from uuid import uuid4


class IntentStatus(StrEnum):
    PENDING = "PENDING"
    EXECUTED = "EXECUTED"
    FAILED = "FAILED"


@dataclass(frozen=True)
class RemediationIntent:
    intent_id: str
    project_id: str
    incident_id: str
    strategy: str
    status: IntentStatus
    created_at: datetime
    executed_at: datetime | None
    evidence_json: str


class IntentStore:
    """Crash-safe remediation intents — write before execute."""

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _ensure_schema(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS remediation_intents (
                    intent_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    incident_id TEXT NOT NULL,
                    strategy TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    executed_at TEXT,
                    evidence_json TEXT NOT NULL
                )
                """
            )

    def create_pending(
        self,
        *,
        project_id: str,
        incident_id: str,
        strategy: str,
        evidence: dict[str, Any],
        intent_id: str | None = None,
        created_at: datetime | None = None,
    ) -> RemediationIntent:
        intent = RemediationIntent(
            intent_id=intent_id or str(uuid4()),
            project_id=project_id,
            incident_id=incident_id,
            strategy=strategy,
            status=IntentStatus.PENDING,
            created_at=created_at or datetime.now(UTC),
            executed_at=None,
            evidence_json=json.dumps(evidence),
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO remediation_intents (
                    intent_id, project_id, incident_id, strategy, status,
                    created_at, executed_at, evidence_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    intent.intent_id,
                    intent.project_id,
                    intent.incident_id,
                    intent.strategy,
                    intent.status.value,
                    intent.created_at.isoformat(),
                    None,
                    intent.evidence_json,
                ),
            )
        return intent

    def mark_executed(self, intent_id: str, *, executed_at: datetime | None = None) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE remediation_intents
                SET status = ?, executed_at = ?
                WHERE intent_id = ?
                """,
                (
                    IntentStatus.EXECUTED.value,
                    (executed_at or datetime.now(UTC)).isoformat(),
                    intent_id,
                ),
            )

    def mark_failed(self, intent_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE remediation_intents SET status = ? WHERE intent_id = ?",
                (IntentStatus.FAILED.value, intent_id),
            )

    def list_orphaned_pending(self, *, project_id: str) -> list[RemediationIntent]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM remediation_intents
                WHERE project_id = ? AND status = ?
                ORDER BY created_at ASC
                """,
                (project_id, IntentStatus.PENDING.value),
            ).fetchall()
        return [self._row_to_intent(row) for row in rows]

    @staticmethod
    def _row_to_intent(row: sqlite3.Row) -> RemediationIntent:
        return RemediationIntent(
            intent_id=row["intent_id"],
            project_id=row["project_id"],
            incident_id=row["incident_id"],
            strategy=row["strategy"],
            status=IntentStatus(row["status"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            executed_at=(
                datetime.fromisoformat(row["executed_at"])
                if row["executed_at"] is not None
                else None
            ),
            evidence_json=row["evidence_json"],
        )
