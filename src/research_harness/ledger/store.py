from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any
from uuid import uuid4


class LedgerEventType(StrEnum):
    STATE_TRANSITION = "state_transition"
    DESIRED_STATE_CHANGE = "desired_state_change"
    RUNTIME_RECONCILIATION = "runtime_reconciliation"
    EXPERIMENT_START = "experiment_start"
    EXPERIMENT_STOP = "experiment_stop"
    CHECKPOINT = "checkpoint"
    INCIDENT = "incident"
    DIAGNOSIS = "diagnosis"
    REMEDIATION = "remediation"
    VERIFICATION = "verification"
    MODEL_SWAP = "model_swap"
    PROVIDER_SWAP = "provider_swap"
    BUDGET = "budget"
    COMPLETION = "completion"


@dataclass(frozen=True)
class LedgerEvent:
    event_id: str
    project_id: str
    contract_version: int
    event_type: LedgerEventType
    recorded_at: datetime
    payload: dict[str, Any]


class LedgerStore:
    """Append-only SQLite event ledger."""

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
                CREATE TABLE IF NOT EXISTS ledger_events (
                    event_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    contract_version INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    recorded_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_ledger_project_recorded
                ON ledger_events (project_id, recorded_at)
                """
            )

    def append(
        self,
        *,
        project_id: str,
        contract_version: int,
        event_type: LedgerEventType | str,
        payload: dict[str, Any],
        recorded_at: datetime | None = None,
        event_id: str | None = None,
    ) -> LedgerEvent:
        event = LedgerEvent(
            event_id=event_id or str(uuid4()),
            project_id=project_id,
            contract_version=contract_version,
            event_type=LedgerEventType(event_type),
            recorded_at=recorded_at or datetime.now(UTC),
            payload=payload,
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO ledger_events (
                    event_id, project_id, contract_version, event_type,
                    recorded_at, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    event.project_id,
                    event.contract_version,
                    event.event_type.value,
                    event.recorded_at.isoformat(),
                    json.dumps(event.payload),
                ),
            )
        return event

    def list_events(
        self,
        *,
        project_id: str | None = None,
        limit: int | None = None,
    ) -> list[LedgerEvent]:
        query = "SELECT * FROM ledger_events"
        params: list[Any] = []
        if project_id is not None:
            query += " WHERE project_id = ?"
            params.append(project_id)
        query += " ORDER BY recorded_at ASC"
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)

        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()

        return [self._row_to_event(row) for row in rows]

    def latest_event(self, *, project_id: str) -> LedgerEvent | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM ledger_events
                WHERE project_id = ?
                ORDER BY recorded_at DESC
                LIMIT 1
                """,
                (project_id,),
            ).fetchone()
        return self._row_to_event(row) if row is not None else None

    @staticmethod
    def _row_to_event(row: sqlite3.Row) -> LedgerEvent:
        return LedgerEvent(
            event_id=row["event_id"],
            project_id=row["project_id"],
            contract_version=row["contract_version"],
            event_type=LedgerEventType(row["event_type"]),
            recorded_at=datetime.fromisoformat(row["recorded_at"]),
            payload=json.loads(row["payload_json"]),
        )
