from __future__ import annotations

from dataclasses import dataclass, field

from research_harness.contract.models import ProjectContract
from research_harness.models.enums import RuntimeFreshness


@dataclass(frozen=True)
class ValidityCheckResult:
    check_id: str
    passed: bool
    on_fail: str
    detail: str | None = None


@dataclass
class ValidityResult:
    passed: bool
    failed_checks: list[ValidityCheckResult] = field(default_factory=list)
    null_rate: float = 0.0
    error_rate: float = 0.0
    fingerprint_match: bool = True
    should_open_incident: bool = False
    should_block: bool = False


def evaluate_validity(
    *,
    contract: ProjectContract,
    completed_units: int,
    null_units: int = 0,
    error_units: int = 0,
    runtime_freshness: RuntimeFreshness = RuntimeFreshness.CURRENT,
    custom_results: dict[str, bool] | None = None,
) -> ValidityResult:
    """Run basic validity gates from the contract."""
    custom_results = custom_results or {}
    total = max(completed_units, 1)
    null_rate = null_units / total
    error_rate = error_units / total

    failed: list[ValidityCheckResult] = []
    if null_rate > contract.validity.max_null_rate:
        failed.append(
            ValidityCheckResult(
                check_id="max_null_rate",
                passed=False,
                on_fail=contract.validity.on_invalid,
                detail=f"null_rate={null_rate:.3f} > {contract.validity.max_null_rate}",
            )
        )
    if error_rate > contract.validity.max_error_rate:
        failed.append(
            ValidityCheckResult(
                check_id="max_error_rate",
                passed=False,
                on_fail=contract.validity.on_invalid,
                detail=f"error_rate={error_rate:.3f} > {contract.validity.max_error_rate}",
            )
        )

    fingerprint_match = True
    if contract.validity.require_fingerprint_match:
        fingerprint_match = runtime_freshness == RuntimeFreshness.CURRENT
        if not fingerprint_match:
            failed.append(
                ValidityCheckResult(
                    check_id="fingerprint_match",
                    passed=False,
                    on_fail="incident",
                    detail="runtime fingerprint stale",
                )
            )

    for check in contract.validity.checks:
        passed = custom_results.get(check.id, True)
        if not passed:
            failed.append(
                ValidityCheckResult(
                    check_id=check.id,
                    passed=False,
                    on_fail=check.on_fail,
                    detail=f"custom check {check.id} failed",
                )
            )

    should_block = any(item.on_fail == "block" for item in failed)
    should_open_incident = bool(failed) and not should_block
    if contract.validity.on_invalid == "incident" and failed:
        should_open_incident = True

    return ValidityResult(
        passed=not failed,
        failed_checks=failed,
        null_rate=null_rate,
        error_rate=error_rate,
        fingerprint_match=fingerprint_match,
        should_open_incident=should_open_incident,
        should_block=should_block,
    )
