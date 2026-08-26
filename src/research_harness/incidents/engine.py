from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from research_harness.adapters.base import CheckpointAdapter, RuntimeAdapter
from research_harness.contract.models import ProjectContract
from research_harness.incidents.models import Incident, IncidentRecordStatus, IncidentStage
from research_harness.incidents.store import IncidentStore
from research_harness.ledger import LedgerEventType, LedgerStore
from research_harness.models.enums import Lifecycle, VerificationLevel
from research_harness.models.state import ObservedState
from research_harness.recovery import IntentStore, RecoveryBudgetTracker
from research_harness.runtime.fingerprint import compare_fingerprints
from research_harness.validity.evaluator import ValidityResult, evaluate_validity
from research_harness.verification import BurnInState, evaluate_burn_in, health_ok, progress_ok
from research_harness.watchdog.evaluator import ProgressContext, WatchdogResult, evaluate_watchdog


@dataclass
class EvaluationResult:
    observed: ObservedState
    watchdog: WatchdogResult
    validity: ValidityResult
    incident: Incident | None = None
    actions_taken: list[str] = field(default_factory=list)
    blocked_reason: str | None = None
    lifecycle: Lifecycle = Lifecycle.RUNNING
    verification_level: VerificationLevel | None = None


class IncidentEngine:
    """Detect operational failures, recover with budgets, verify to stable."""

    def __init__(
        self,
        *,
        contract: ProjectContract,
        runtime: RuntimeAdapter,
        checkpoint: CheckpointAdapter | None = None,
        incident_store: IncidentStore | None = None,
        intent_store: IntentStore | None = None,
        ledger: LedgerStore | None = None,
    ) -> None:
        self.contract = contract
        self.runtime = runtime
        self.checkpoint = checkpoint
        self.incident_store = incident_store
        self.intent_store = intent_store
        self.ledger = ledger

    def reconcile_orphaned_intents(self) -> list[str]:
        """Reconcile pending intents left by a crashed supervisor."""
        if self.intent_store is None:
            return []
        actions: list[str] = []
        for intent in self.intent_store.list_orphaned_pending(
            project_id=self.contract.project.id
        ):
            evidence = {"orphaned_intent": intent.intent_id, "strategy": intent.strategy}
            incident = (
                self.incident_store.get(intent.incident_id)
                if self.incident_store is not None
                else None
            )
            if incident is None:
                self.intent_store.mark_failed(intent.intent_id)
                continue
            if self._execute_strategy(incident, intent.strategy, evidence):
                actions.append(f"executed:{intent.strategy}")
            self.intent_store.mark_executed(intent.intent_id)
            actions.append(f"orphan_reconciled:{intent.intent_id}")
        return actions

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

        result = EvaluationResult(
            observed=observed,
            watchdog=watchdog,
            validity=validity,
            lifecycle=Lifecycle.RUNNING,
        )
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
            remediate = self._remediate(incident, now=evaluated_at)
            result.actions_taken.extend(remediate.actions)
            result.blocked_reason = remediate.blocked_reason
            if remediate.blocked_reason:
                result.lifecycle = Lifecycle.BLOCKED
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
            burn_in = self._burn_in_state(incident)

            if watchdog.should_open_incident and burn_in.level != VerificationLevel.STABLE:
                if not incident.evidence.get("blocked"):
                    remediate = self._remediate(incident, now=evaluated_at)
                    result.actions_taken.extend(remediate.actions)
                    if remediate.blocked_reason:
                        result.blocked_reason = remediate.blocked_reason
                        result.lifecycle = Lifecycle.BLOCKED
                    burn_in = self._burn_in_state(incident)
            else:
                burn_in = evaluate_burn_in(
                    contract=self.contract,
                    observed=observed,
                    health_ok=health_ok(observed),
                    progress_ok=progress_ok(watchdog.progress),
                    state=burn_in,
                    now=evaluated_at,
                    patched=bool(incident.remediations),
                )
                incident.evidence["burn_in"] = burn_in.to_dict()
                if self.incident_store is not None:
                    self.incident_store.update(incident)

            result.verification_level = burn_in.level
            if burn_in.level == VerificationLevel.STABLE:
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
        incident.evidence["recovery_attempts"] = []
        incident.evidence["burn_in"] = BurnInState().to_dict()
        if self.incident_store is not None:
            self.incident_store.update(incident)
        self._record_ledger(
            event_type=LedgerEventType.INCIDENT,
            payload={"action": "open", "incident_id": incident.incident_id, "symptom": symptom},
        )
        return incident

    @dataclass
    class _RemediateResult:
        actions: list[str]
        blocked_reason: str | None = None

    def _remediate(self, incident: Incident, *, now: datetime) -> _RemediateResult:
        actions: list[str] = []
        if not self.contract.authority.runtime_restarts:
            reason = (
                "Recovery blocked: authority.runtime_restarts is false; "
                f"symptom={incident.symptom}"
            )
            incident.evidence["blocked"] = reason
            incident.stage = IncidentStage.DIAGNOSE
            if self.incident_store is not None:
                self.incident_store.update(incident)
            return self._RemediateResult(actions=actions, blocked_reason=reason)

        tracker = self._recovery_tracker(incident)
        decision = tracker.next_strategy(
            evidence=incident.evidence,
            incident_opened_at=incident.opened_at,
            now=now,
        )
        if not decision.allowed or decision.strategy is None:
            reason = decision.blocked_reason or "recovery not allowed"
            incident.evidence["blocked"] = reason
            if self.incident_store is not None:
                self.incident_store.update(incident)
            return self._RemediateResult(actions=actions, blocked_reason=reason)

        strategy = decision.strategy
        intent_id: str | None = None
        if self.intent_store is not None:
            intent = self.intent_store.create_pending(
                project_id=self.contract.project.id,
                incident_id=incident.incident_id,
                strategy=strategy,
                evidence=incident.evidence,
                created_at=now,
            )
            intent_id = intent.intent_id

        incident.stage = IncidentStage.REMEDIATE
        succeeded = self._execute_strategy(incident, strategy, incident.evidence)
        tracker.record_attempt(
            strategy=strategy,
            evidence=incident.evidence,
            now=now,
            succeeded=succeeded,
        )
        incident.evidence["recovery_attempts"] = tracker.to_evidence()
        incident.remediations.append(strategy)
        actions.append(strategy)

        if self.checkpoint is not None and succeeded:
            checkpoint = self.checkpoint.latest_checkpoint()
            if checkpoint is not None:
                incident.evidence["checkpoint_resumed"] = checkpoint
                actions.append("checkpoint_resume")

        if intent_id is not None and self.intent_store is not None:
            if succeeded:
                self.intent_store.mark_executed(intent_id, executed_at=now)
            else:
                self.intent_store.mark_failed(intent_id)

        burn_in = evaluate_burn_in(
            contract=self.contract,
            observed=self.runtime.inspect(),
            health_ok=succeeded,
            progress_ok=succeeded,
            state=BurnInState(),
            now=now,
            patched=succeeded,
        )
        incident.evidence["burn_in"] = burn_in.to_dict()
        incident.stage = IncidentStage.VERIFY if succeeded else IncidentStage.DIAGNOSE
        if self.incident_store is not None:
            self.incident_store.update(incident)
        self._record_ledger(
            event_type=LedgerEventType.REMEDIATION,
            payload={
                "incident_id": incident.incident_id,
                "actions": actions,
                "succeeded": succeeded,
                "intent_id": intent_id,
            },
        )
        return self._RemediateResult(actions=actions)

    def _execute_strategy(
        self,
        incident: Incident,
        strategy: str,
        evidence: dict[str, Any],
    ) -> bool:
        del incident, evidence
        executor = getattr(self.runtime, "execute_recovery_strategy", None)
        if callable(executor):
            return bool(executor(strategy))
        if strategy == "worker_restart":
            self.runtime.restart_worker()
            return True
        if strategy == "service_restart":
            self.runtime.relaunch("service_restart")
            return True
        if strategy == "full_relaunch":
            self.runtime.relaunch("full_relaunch")
            return True
        return False

    def _close_incident(self, incident: Incident) -> list[str]:
        incident.stage = IncidentStage.CLOSE
        incident.status = IncidentRecordStatus.CLOSED
        incident.closed_at = datetime.now(UTC)
        incident.resolution = "stable_after_burn_in"
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
        self._record_ledger(
            event_type=LedgerEventType.VERIFICATION,
            payload={
                "incident_id": incident.incident_id,
                "level": VerificationLevel.STABLE.value,
            },
        )
        return ["incident_closed"]

    def _recovery_tracker(self, incident: Incident) -> RecoveryBudgetTracker:
        raw = incident.evidence.get("recovery_attempts", [])
        if isinstance(raw, list) and raw:
            return RecoveryBudgetTracker.from_evidence(self.contract, raw)
        return RecoveryBudgetTracker(contract=self.contract)

    def _burn_in_state(self, incident: Incident) -> BurnInState:
        raw = incident.evidence.get("burn_in")
        if isinstance(raw, dict):
            return BurnInState.from_dict(raw)
        return BurnInState()

    def _record_ledger(self, *, event_type: LedgerEventType, payload: dict[str, Any]) -> None:
        if self.ledger is None:
            return
        self.ledger.append(
            project_id=self.contract.project.id,
            contract_version=self.contract.contract_version,
            event_type=event_type,
            payload=payload,
        )
