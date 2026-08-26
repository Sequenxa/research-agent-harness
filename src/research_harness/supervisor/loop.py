from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from research_harness.adapters.base import RuntimeAdapter
from research_harness.completion import evaluate_completion
from research_harness.contract.models import ProjectContract
from research_harness.incidents import IncidentEngine, IncidentStore
from research_harness.invariants import evaluate_invariants
from research_harness.ledger import LedgerEventType, LedgerStore
from research_harness.models.enums import Lifecycle
from research_harness.models.state import ObservedState
from research_harness.recovery import IntentStore
from research_harness.reconciliation import Reconciler
from research_harness.supervisor.escalation import EscalationManager
from research_harness.supervisor.lease import ProjectLease
from research_harness.supervisor.runtime_factory import (
    as_checkpoint,
    as_diagnostics,
    desired_fingerprint_for,
    progress_context_for,
)
from research_harness.supervisor.stop import clear_stop, stop_requested

RuntimeKind = Literal["file", "failing-worker"]


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
        owner: str = "supervisor",
    ) -> None:
        self.contract = contract
        self.runtime = runtime
        self.state_dir = Path(state_dir)
        self.ledger = ledger
        self.runtime_kind = runtime_kind
        self.owner = owner
        self.lease = ProjectLease(state_dir=self.state_dir, project_id=contract.project.id)
        self.escalation = EscalationManager(state_dir=self.state_dir, contract=contract)
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
        )
        self.reconciler = Reconciler(contract=contract, runtime=runtime, ledger=ledger)
        self._spend_usd = 0.0
        self._started = False

    def startup(self) -> list[str]:
        actions: list[str] = []
        if not self.lease.acquire(self.owner):
            msg = f"Lease held by another supervisor for project {self.contract.project.id}"
            raise RuntimeError(msg)
        actions.extend(self.incident_engine.reconcile_orphaned_intents())
        self.ledger.append(
            project_id=self.contract.project.id,
            contract_version=self.contract.contract_version,
            event_type=LedgerEventType.EXPERIMENT_START,
            payload={"owner": self.owner, "runtime": self.runtime_kind},
        )
        clear_stop(self.state_dir)
        self._started = True
        return actions

    def shutdown(self) -> None:
        self.lease.release(self.owner)
        self.ledger.append(
            project_id=self.contract.project.id,
            contract_version=self.contract.contract_version,
            event_type=LedgerEventType.EXPERIMENT_STOP,
            payload={"owner": self.owner},
        )

    def tick(self, *, now: datetime | None = None) -> TickResult:
        evaluated_at = now or datetime.now(UTC)
        if stop_requested(self.state_dir):
            return TickResult(
                observed=self.runtime.inspect(),
                lifecycle=Lifecycle.STOPPED,
                stopped=True,
                message="stop flag set",
            )

        timeout_state = self.escalation.check_timeout(now=evaluated_at, ledger=self.ledger)
        if timeout_state is not None and self.contract.escalation.on_timeout == "stop":
            self._stop_runtime()
            return TickResult(
                observed=self.runtime.inspect(),
                lifecycle=Lifecycle.STOPPED,
                stopped=True,
                message="escalation timeout — workers stopped",
            )

        invariants = evaluate_invariants(self.contract, ledger=self.ledger)
        if not invariants.passed:
            if invariants.violation_action == "halt":
                self._stop_runtime()
                return TickResult(
                    observed=self.runtime.inspect(),
                    lifecycle=Lifecycle.BLOCKED,
                    blocked=True,
                    message="invariant violation halt",
                )

        desired_fp = desired_fingerprint_for(
            self.runtime, state_dir=self.state_dir, runtime_kind=self.runtime_kind
        )
        observed = self.runtime.inspect()
        progress = progress_context_for(self.runtime)
        evaluation = self.incident_engine.evaluate(
            observed=observed,
            progress=progress,
            desired_fingerprint=desired_fp,
            now=evaluated_at,
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
        if reconcile.actions_taken:
            self._record_spend(0.10)

        observed = self.runtime.inspect()
        completion = evaluate_completion(
            contract=self.contract,
            observed=observed,
            validity=evaluation.validity,
        )
        if completion.met:
            self.ledger.append(
                project_id=self.contract.project.id,
                contract_version=self.contract.contract_version,
                event_type=LedgerEventType.COMPLETION,
                payload={"reason": completion.reason, "units": observed.completed_units},
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
                if result.stopped or result.completed:
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
        if self.lease.is_held_by_other(self.owner):
            checks.append("lease: held by another supervisor")
        else:
            checks.append("lease: available or held by this owner")
        if stop_requested(self.state_dir):
            checks.append("stop flag: set")
        else:
            checks.append("stop flag: clear")
        escalation = self.escalation.load()
        if escalation.blocked:
            checks.append(f"escalation: blocked ({escalation.reason})")
        else:
            checks.append("escalation: none")
        return DoctorReport(ok=ok, checks=checks)

    def _stop_runtime(self) -> None:
        stopper = getattr(self.runtime, "stop", None)
        if callable(stopper):
            stopper()
        else:
            self.runtime.restart_worker()

    def _record_spend(self, amount: float) -> None:
        self._spend_usd += amount
        if self.contract.budget.total_usd and self._spend_usd > self.contract.budget.total_usd:
            self.escalation.mark_blocked("budget.total_usd exceeded")
        self.ledger.append(
            project_id=self.contract.project.id,
            contract_version=self.contract.contract_version,
            event_type=LedgerEventType.BUDGET,
            payload={"spend_usd": self._spend_usd, "delta_usd": amount},
        )
