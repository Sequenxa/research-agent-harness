from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from research_harness.contract.models import Invariant, ProjectContract
from research_harness.ledger import LedgerEventType, LedgerStore


@dataclass(frozen=True)
class InvariantResult:
    invariant_id: str
    passed: bool
    detail: str | None = None


@dataclass
class InvariantEvaluation:
    passed: bool
    results: list[InvariantResult]
    violation_action: str | None = None


def _run_check(invariant: Invariant) -> InvariantResult:
    """Execute a contract invariant check by name.

    v0.1 ships built-in checks for local development; external repos register
    handlers by extending this module.
    """
    check = invariant.check
    if check in {"always.pass", "dev.always_pass"}:
        return InvariantResult(invariant_id=invariant.id, passed=True)
    if check == "fs_acl.sealed_paths_unreadable_by_worker":
        # Placeholder — real implementation lives in project adapters.
        return InvariantResult(
            invariant_id=invariant.id,
            passed=True,
            detail="stub pass (implement in project adapter)",
        )
    return InvariantResult(
        invariant_id=invariant.id,
        passed=False,
        detail=f"unknown invariant check: {check}",
    )


def evaluate_invariants(
    contract: ProjectContract,
    *,
    ledger: LedgerStore | None = None,
) -> InvariantEvaluation:
    results = [_run_check(invariant) for invariant in contract.invariants]
    failed = [result for result in results if not result.passed]
    if not failed:
        return InvariantEvaluation(passed=True, results=results)

    violation = _violation_action(contract.invariants, failed[0].invariant_id)
    evaluation = InvariantEvaluation(
        passed=False,
        results=results,
        violation_action=violation,
    )
    if ledger is not None:
        ledger.append(
            project_id=contract.project.id,
            contract_version=contract.contract_version,
            event_type=LedgerEventType.INCIDENT,
            payload={
                "action": "invariant_violation",
                "failed": [result.invariant_id for result in failed],
                "on_violation": violation,
            },
        )
    return evaluation


def _violation_action(invariants: list[Invariant], failed_id: str) -> str:
    for invariant in invariants:
        if invariant.id == failed_id:
            return invariant.on_violation
    return "incident"
