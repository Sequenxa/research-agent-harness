from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from research_harness.adapters.base import CheckpointAdapter, RuntimeAdapter
from research_harness.contract.models import ProjectContract
from research_harness.incidents.models import Incident, IncidentRecordStatus, IncidentStage
from research_harness.incidents.store import IncidentStore
from research_harness.ledger import LedgerEventType, LedgerStore
from research_harness.models.enums import Health, Progress
from research_harness.models.state import ObservedState
from research_harness.runtime.fingerprint import compare_fingerprints
from research_harness.validity.evaluator import ValidityResult, evaluate_validity
from research_harness.watchdog.evaluator import ProgressContext, WatchdogResult, evaluate_watchdog


@dataclass
class EvaluationResult:
    observed: ObservedState
    watchdog: WatchdogResult
    validity: ValidityResult
    incident: Incident | None = None
    actions_taken: list[str] = field(default_factory=list)


class IncidentEngine:
    """Detect operational failures and apply basic recovery."""

    def __init__(
        self,
        *,
        contract: ProjectContract,
        runtime: RuntimeAdapter,
        checkpoint: CheckpointAdapter | None = None,
        incident_store: IncidentStore | None = None,
        ledger: LedgerStore | None = None,
    ) -> None:
        self.contract = contract
        self.runtime = runtime
        self.checkpoint = checkpoint
        self.incident_store = incident_store
        self.ledger = ledger

    def evaluate(
        self,
        *,
        observed: ObservedState,
        progress: ProgressContext,
        desired_fingerprint: dict[str, str],
        custom_validity: dict[str, bool] | None = None,
        now: datetime | None = None,
    ) -> EvaluationResult:
        evaluated_at = now or datetime.now(UTC)
        watchdog = evaluate_watchdog(
            contract=self.contract,
            observed=observed,
            progress=progress,
            now=evaluated_at,
        )
        freshness = compare_fingerprints(
            desired=desired_fingerprint,
            observed=observed.fingerprint,
            fields=self.contract.fingerprint.fields,
        ).freshness
        validity = evaluate_validity(
            contract=self.contract,
            completed_units=observed.completed_units,
            runtime_freshness=freshness,
            custom_results=custom_validity,
        )

        result = EvaluationResult(observed=observed, watchdog=watchdog, validity=validity)
        open_incidents = (
            self.incident_store.list_open(project_id=self.contract.project.id)
            if self.incident_store is not None
            else []
        )

        if watchdog.should_open_incident and not open_incidents:
            incident = self._open_incident(
                symptom=watchdog.symptom or "unknown",
                evidence={
                    "health": watchdog.health.value,
                    "progress": watchdog.progress.value,
                    "stalled_watermarks": list(watchdog.stalled_watermarks),
                    "stalled_phases": list(watchdog.stalled_phases),
                    "observed_at": observed.observed_at.isoformat(),
                },
            )
            result.incident = incident
            result.actions_taken.extend(self._remediate(incident))
        elif validity.should_open_incident and not open_incidents:
            incident = self._open_incident(
                symptom="validity_failed",
                evidence={
                    "failed_checks": [c.check_id for c in validity.failed_checks],
                    "null_rate": validity.null_rate,
                    "error_rate": validity.error_rate,
                },
            )
            result.incident = incident
        elif open_incidents:
            incident = open_incidents[0]
            result.incident = incident
            if self._should_close(watchdog=watchdog, observed=observed):
                result.actions_taken.extend(self._close_incident(incident))

        return result

    def _open_incident(self, *, symptom: str, evidence: dict[str, Any]) -> Incident:
        if self.incident_store is None:
            msg = "Incident store is required to open incidents"
            raise RuntimeError(msg)
        incident = self.incident_store.create(
            project_id=self.contract.project.id,
            contract_version=self.contract.contract_version,
            symptom=symptom,
            evidence=evidence,
        )
        self._record_ledger(
            event_type=LedgerEventType.INCIDENT,
            payload={"action": "open", "incident_id": incident.incident_id, "symptom": symptom},
        )
        return incident

    def _remediate(self, incident: Incident) -> list[str]:
        actions: list[str] = []
        if not self.contract.authority.runtime_restarts:
            incident.evidence["blocked"] = "runtime_restarts not authorized"
            if self.incident_store is not None:
                self.incident_store.update(incident)
            return actions

        incident.stage = IncidentStage.REMEDIATE
        if incident.symptom in {"worker_unhealthy", "progress_stalled"}:
            self.runtime.restart_worker()
            actions.append("worker_restart")
            incident.remediations.append("worker_restart")
            if self.checkpoint is not None:
                checkpoint = self.checkpoint.latest_checkpoint()
                if checkpoint is not None:
                    incident.evidence["checkpoint_resumed"] = checkpoint
                    actions.append("checkpoint_resume")

        incident.stage = IncidentStage.VERIFY
        if self.incident_store is not None:
            self.incident_store.update(incident)
        self._record_ledger(
            event_type=LedgerEventType.REMEDIATION,
            payload={
                "incident_id": incident.incident_id,
                "actions": actions,
            },
        )
        return actions

    def _should_close(self, *, watchdog: WatchdogResult, observed: ObservedState) -> bool:
        return (
            watchdog.health == Health.HEALTHY
            and watchdog.progress == Progress.ADVANCING
            and observed.completed_units > 0
        )

    def _close_incident(self, incident: Incident) -> list[str]:
        incident.stage = IncidentStage.CLOSE
        incident.status = IncidentRecordStatus.CLOSED
        incident.closed_at = datetime.now(UTC)
        incident.resolution = "recovered_and_progressing"
        if self.incident_store is not None:
            self.incident_store.update(incident)
        self._record_ledger(
            event_type=LedgerEventType.INCIDENT,
            payload={
                "action": "close",
                "incident_id": incident.incident_id,
                "resolution": incident.resolution,
            },
        )
        return ["incident_closed"]

    def _record_ledger(self, *, event_type: LedgerEventType, payload: dict[str, Any]) -> None:
        if self.ledger is None:
            return
        self.ledger.append(
            project_id=self.contract.project.id,
            contract_version=self.contract.contract_version,
            event_type=event_type,
            payload=payload,
        )
