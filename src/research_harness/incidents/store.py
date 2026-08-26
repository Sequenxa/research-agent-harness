from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from research_harness.incidents.models import Incident, IncidentRecordStatus, IncidentStage


class IncidentStore:
    """SQLite store for incident records."""

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
                CREATE TABLE IF NOT EXISTS incidents (
                    incident_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    contract_version INTEGER NOT NULL,
                    symptom TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    status TEXT NOT NULL,
                    opened_at TEXT NOT NULL,
                    closed_at TEXT,
                    evidence_json TEXT NOT NULL,
                    remediations_json TEXT NOT NULL,
                    resolution TEXT
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_incidents_project_status
                ON incidents (project_id, status)
                """
            )

    def create(
        self,
        *,
        project_id: str,
        contract_version: int,
        symptom: str,
        evidence: dict[str, object],
        incident_id: str | None = None,
        opened_at: datetime | None = None,
    ) -> Incident:
        incident = Incident(
            incident_id=incident_id or str(uuid4()),
            project_id=project_id,
            contract_version=contract_version,
            symptom=symptom,
            opened_at=opened_at or datetime.now(UTC),
            evidence=evidence,
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO incidents (
                    incident_id, project_id, contract_version, symptom, stage, status,
                    opened_at, closed_at, evidence_json, remediations_json, resolution
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    incident.incident_id,
                    incident.project_id,
                    incident.contract_version,
                    incident.symptom,
                    incident.stage.value,
                    incident.status.value,
                    incident.opened_at.isoformat(),
                    None,
                    json.dumps(incident.evidence),
                    json.dumps(incident.remediations),
                    incident.resolution,
                ),
            )
        return incident

    def get(self, incident_id: str) -> Incident | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM incidents WHERE incident_id = ?",
                (incident_id,),
            ).fetchone()
        return self._row_to_incident(row) if row is not None else None

    def list_open(self, *, project_id: str) -> list[Incident]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM incidents
                WHERE project_id = ? AND status = ?
                ORDER BY opened_at ASC
                """,
                (project_id, IncidentRecordStatus.OPEN.value),
            ).fetchall()
        return [self._row_to_incident(row) for row in rows]

    def update(self, incident: Incident) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE incidents SET
                    stage = ?, status = ?, closed_at = ?, evidence_json = ?,
                    remediations_json = ?, resolution = ?
                WHERE incident_id = ?
                """,
                (
                    incident.stage.value,
                    incident.status.value,
                    incident.closed_at.isoformat() if incident.closed_at else None,
                    json.dumps(incident.evidence),
                    json.dumps(incident.remediations),
                    incident.resolution,
                    incident.incident_id,
                ),
            )

    @staticmethod
    def _row_to_incident(row: sqlite3.Row) -> Incident:
        return Incident(
            incident_id=row["incident_id"],
            project_id=row["project_id"],
            contract_version=row["contract_version"],
            symptom=row["symptom"],
            stage=IncidentStage(row["stage"]),
            status=IncidentRecordStatus(row["status"]),
            opened_at=datetime.fromisoformat(row["opened_at"]),
            closed_at=(
                datetime.fromisoformat(row["closed_at"]) if row["closed_at"] is not None else None
            ),
            evidence=json.loads(row["evidence_json"]),
            remediations=json.loads(row["remediations_json"]),
            resolution=row["resolution"],
        )
