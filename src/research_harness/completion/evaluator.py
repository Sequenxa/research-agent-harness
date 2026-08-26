from __future__ import annotations

from dataclasses import dataclass

from research_harness.contract.models import ProjectContract
from research_harness.models.state import ObservedState
from research_harness.validity.evaluator import ValidityResult


@dataclass(frozen=True)
class CompletionResult:
    met: bool
    reason: str


def evaluate_completion(
    *,
    contract: ProjectContract,
    observed: ObservedState,
    validity: ValidityResult,
) -> CompletionResult:
    """Evaluate the contract completion condition (v0.1 supported forms)."""
    condition = contract.completion.condition.strip()
    units_ok = observed.completed_units >= contract.validity.expected_units
    validity_ok = validity.passed

    if condition == "units_completed >= 1000 and validity.passed":
        met = units_ok and validity_ok
    elif "units_completed >=" in condition and "validity.passed" in condition:
        met = units_ok and validity_ok
    elif condition.startswith("units_completed >="):
        threshold = _parse_units_threshold(condition)
        met = observed.completed_units >= threshold
    else:
        return CompletionResult(
            met=False,
            reason=f"unsupported completion condition: {condition}",
        )

    if met:
        return CompletionResult(met=True, reason="completion condition satisfied")
    return CompletionResult(
        met=False,
        reason=f"units={observed.completed_units}/{contract.validity.expected_units}, validity={validity_ok}",
    )


def _parse_units_threshold(condition: str) -> int:
    # units_completed >= 1000
    parts = condition.replace("units_completed >=", "").strip()
    return int(parts.split()[0])
