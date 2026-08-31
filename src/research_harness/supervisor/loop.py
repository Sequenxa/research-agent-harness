from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from research_harness.adapters.base import RuntimeAdapter
from research_harness.budget import BudgetTracker
from research_harness.completion import evaluate_completion, execute_completion_actions
from research_harness.contract.models import ProjectContract
from research_harness.experiment.plan import (
    load_plan_for_contract,
    resolve_schedule_hash_for_contract,
)
from research_harness.incidents import IncidentEngine, IncidentStore
from research_harness.invariants import evaluate_invariants
from research_harness.invariants.runner import InvariantEvaluation
from research_harness.ledger import LedgerEventType, LedgerStore
from research_harness.models.enums import Lifecycle
from research_harness.models.state import ObservedState
from research_harness.reconciliation import Reconciler
from research_harness.recovery import IntentStore
from research_harness.supervisor.escalation import EscalationManager
from research_harness.supervisor.lease import ProjectLease
from research_harness.supervisor.runtime_factory import (
    as_checkpoint,
    as_diagnostics,
    custom_validity_for,
    desired_fingerprint_for,
    progress_context_for,
)
from research_harness.supervisor.stop import clear_stop, stop_requested

RuntimeKind = str
ACTION_COST_USD = 0.10


@dataclass
class TickResult:
    observed: ObservedState
    lifecycle: Lifecycle
    completed: bool = False
    stopped: bool = False
    blocked: bool = False
    actions: list[str] = field(default_factory=list)
    message: str | None = None


@dataclass
class DoctorReport:
    ok: bool
    checks: list[str]


class Supervisor:
    """Single reconciliation loop (Section 14)."""

    def __init__(
        self,
        *,
        contract: ProjectContract,
        runtime: RuntimeAdapter,
        state_dir: Path,
        ledger: LedgerStore,
        runtime_kind: RuntimeKind = "file",
        instance_id: str | None = None,
        observe_only: bool = False,
    ) -> None:
        self.contract = contract
        self.runtime = runtime
        self.state_dir = Path(state_dir)
        self.ledger = ledger
        self.runtime_kind = runtime_kind
        self.instance_id = instance_id or str(uuid4())
        self.observe_only = observe_only
        self.lease = ProjectLease(state_dir=self.state_dir, project_id=contract.project.id)
        self.escalation = EscalationManager(state_dir=self.state_dir, contract=contract)
        self.budget = BudgetTracker(state_dir=self.state_dir, contract=contract)
        self.incidents = IncidentStore(self.state_dir / "incidents.db")
        self.intents = IntentStore(self.state_dir / "intents.db")
        self.incident_engine = IncidentEngine(
            contract=contract,
            runtime=runtime,
            checkpoint=as_checkpoint(runtime),
            diagnostics=as_diagnostics(runtime),
            incident_store=self.incidents,
            intent_store=self.intents,
            ledger=ledger,
            observe_only=observe_only,
        )
        desired_path = self.state_dir / "desired_fingerprint.json"
        self.reconciler = Reconciler(
            contract=contract,
            runtime=runtime,
            ledger=ledger,
            observe_only=observe_only,
            budget_tracker=self.budget,
            action_cost_usd=ACTION_COST_USD,
            persist_desired_path=desired_path if desired_path.exists() else None,
        )
        self._started = False

    def startup(self) -> list[str]:
        actions: list[str] = []
        if not self.lease.acquire(self.instance_id):
            msg = f"Lease held by another supervisor for project {self.contract.project.id}"
            raise RuntimeError(msg)
        actions.extend(self.incident_engine.reconcile_orphaned_intents())
        self.ledger.append(
            project_id=self.contract.project.id,
            contract_version=self.contract.contract_version,
            event_type=LedgerEventType.EXPERIMENT_START,
            payload={
                "instance_id": self.instance_id,
                "runtime": self.runtime_kind,
                "observe_only": self.observe_only,
            },
        )
        clear_stop(self.state_dir)
        self._started = True
        return actions

    def shutdown(self) -> None:
        self.lease.release(self.instance_id)
        self.ledger.append(
            project_id=self.contract.project.id,
            contract_version=self.contract.contract_version,
            event_type=LedgerEventType.EXPERIMENT_STOP,
            payload={"instance_id": self.instance_id},
        )

    def tick(self, *, now: datetime | None = None) -> TickResult:
        evaluated_at = now or datetime.now(UTC)
        if self._started and not self.lease.renew(self.instance_id):
            return TickResult(
                observed=self.runtime.inspect(),
                lifecycle=Lifecycle.BLOCKED,
                blocked=True,
                message="lease lost or held by another supervisor",
            )

        escalation_state = self.escalation.load()

        timeout_state = self.escalation.check_timeout(now=evaluated_at, ledger=self.ledger)
        if timeout_state is not None and self.contract.escalation.on_timeout == "stop":
            if self.observe_only:
                return TickResult(
                    observed=self.runtime.inspect(),
                    lifecycle=Lifecycle.BLOCKED,
                    blocked=True,
                    actions=["would_stop_workers"],
                    message="escalation timeout — would stop workers (observe_only)",
                )
            if not self._stop_runtime():
                return TickResult(
                    observed=self.runtime.inspect(),
                    lifecycle=Lifecycle.BLOCKED,
                    blocked=True,
                    message="escalation timeout — stop could not be verified",
                )
            return TickResult(
                observed=self.runtime.inspect(),
                lifecycle=Lifecycle.STOPPED,
                stopped=True,
                message="escalation timeout — workers stopped",
            )

        if escalation_state.blocked:
            return TickResult(
                observed=self.runtime.inspect(),
                lifecycle=Lifecycle.BLOCKED,
                blocked=True,
                message=escalation_state.reason,
            )

        if stop_requested(self.state_dir):
            if not self.observe_only and not self._stop_runtime():
                return TickResult(
                    observed=self.runtime.inspect(),
                    lifecycle=Lifecycle.BLOCKED,
                    blocked=True,
                    message="stop requested but runtime could not be stopped",
                )
            return TickResult(
                observed=self.runtime.inspect(),
                lifecycle=Lifecycle.STOPPED,
                stopped=True,
                message="stop flag set",
            )

        invariants = evaluate_invariants(self.contract, ledger=self.ledger)
        if not invariants.passed:
            violation = invariants.violation_action or "incident"
            if violation == "halt":
                if not self.observe_only:
                    self._stop_runtime()
                return TickResult(
                    observed=self.runtime.inspect(),
                    lifecycle=Lifecycle.BLOCKED,
                    blocked=True,
                    message="invariant violation halt",
                )
            if violation == "block":
                return TickResult(
                    observed=self.runtime.inspect(),
                    lifecycle=Lifecycle.BLOCKED,
                    blocked=True,
                    message="invariant violation block",
                )
            self._record_invariant_incident(invariants)
            return TickResult(
                observed=self.runtime.inspect(),
                lifecycle=Lifecycle.RUNNING,
                actions=["invariant_incident"],
                message="invariant violation incident opened",
            )

        desired_fp = desired_fingerprint_for(
            self.runtime, state_dir=self.state_dir, runtime_kind=self.runtime_kind
        )
        observed = self.runtime.inspect()
        progress = progress_context_for(self.runtime)
        project_root = self.state_dir.parent
        plan = None
        schedule_hash = None
        if self.contract.experiment is not None:
            plan = load_plan_for_contract(self.contract, base_dir=project_root)
            schedule_hash = resolve_schedule_hash_for_contract(
                self.contract, plan=plan, base_dir=project_root
            )
        evaluation = self.incident_engine.evaluate(
            observed=observed,
            progress=progress,
            desired_fingerprint=desired_fp,
            custom_validity=custom_validity_for(self.runtime),
            now=evaluated_at,
            plan=plan,
            observed_schedule_hash=schedule_hash,
        )
        actions = list(evaluation.actions_taken)

        if evaluation.blocked_reason:
            self.escalation.mark_blocked(evaluation.blocked_reason, now=evaluated_at)
            return TickResult(
                observed=evaluation.observed,
                lifecycle=Lifecycle.BLOCKED,
                blocked=True,
                actions=actions,
                message=evaluation.blocked_reason,
            )

        reconcile = self.reconciler.reconcile(desired_fingerprint=desired_fp)
        actions.extend(reconcile.actions_taken)
        if not reconcile.success:
            reason = reconcile.blocked_reason or "reconciliation blocked"
            self.escalation.mark_blocked(reason, now=evaluated_at)
            return TickResult(
                observed=self.runtime.inspect(),
                lifecycle=Lifecycle.BLOCKED,
                blocked=True,
                actions=actions,
                message=reason,
            )

        observed = self.runtime.inspect()
        completion = evaluate_completion(
            contract=self.contract,
            observed=observed,
            validity=evaluation.validity,
            plan=plan,
        )
        if completion.met:
            completion_actions = execute_completion_actions(
                contract=self.contract,
                runtime=self.runtime,
                ledger=self.ledger,
                state_dir=self.state_dir,
                observe_only=self.observe_only,
            )
            actions.extend(completion_actions)
            self.ledger.append(
                project_id=self.contract.project.id,
                contract_version=self.contract.contract_version,
                event_type=LedgerEventType.COMPLETION,
                payload={
                    "reason": completion.reason,
                    "units": observed.completed_units,
                    "actions": completion_actions,
                },
            )
            return TickResult(
                observed=observed,
                lifecycle=Lifecycle.COMPLETED,
                completed=True,
                actions=actions,
                message=completion.reason,
            )

        lifecycle = Lifecycle.RUNNING
        if evaluation.lifecycle == Lifecycle.BLOCKED:
            lifecycle = Lifecycle.BLOCKED
        return TickResult(
            observed=observed,
            lifecycle=lifecycle,
            actions=actions,
        )

    def run(
        self,
        *,
        interval_seconds: float = 1.0,
        max_ticks: int | None = None,
    ) -> list[TickResult]:
        if not self._started:
            self.startup()
        ticks = 0
        results: list[TickResult] = []
        try:
            while True:
                result = self.tick()
                results.append(result)
                if result.stopped or result.completed or result.blocked:
                    break
                ticks += 1
                if max_ticks is not None and ticks >= max_ticks:
                    break
                time.sleep(interval_seconds)
        finally:
            if self._started:
                self.shutdown()
        return results

    def doctor(self) -> DoctorReport:
        checks: list[str] = []
        ok = True
        if self.state_dir.exists():
            checks.append(f"state_dir ok: {self.state_dir}")
        else:
            ok = False
            checks.append(f"state_dir missing: {self.state_dir}")
        if self.lease.is_held_by_other(self.instance_id):
            checks.append("lease: held by another supervisor")
        else:
            checks.append("lease: available or held by this instance")
        if stop_requested(self.state_dir):
            checks.append("stop flag: set")
        else:
            checks.append("stop flag: clear")
        escalation = self.escalation.load()
        if escalation.blocked:
            checks.append(f"escalation: blocked ({escalation.reason})")
        else:
            checks.append("escalation: none")
        checks.append(f"observe_only: {self.observe_only}")
        checks.append(f"budget spend_usd: {self.budget.state.spend_usd}")
        return DoctorReport(ok=ok, checks=checks)

    def _stop_runtime(self) -> bool:
        if self.observe_only:
            return False
        return self.runtime.stop_verified()

    def _record_invariant_incident(self, invariants: InvariantEvaluation) -> None:
        failed = [result.invariant_id for result in invariants.results if not result.passed]
        if self.observe_only:
            self.ledger.append(
                project_id=self.contract.project.id,
                contract_version=self.contract.contract_version,
                event_type=LedgerEventType.INCIDENT,
                payload={"observe_only": True, "action": "would_open", "failed": failed},
            )
            return
        self.incidents.create(
            project_id=self.contract.project.id,
            contract_version=self.contract.contract_version,
            symptom="invariant_violation",
            evidence={"failed": failed},
        )
