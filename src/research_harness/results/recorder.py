from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel

from research_harness.ledger import LedgerEventType, LedgerStore
from research_harness.models.state import ObservedState
from research_harness.validity.evaluator import ValidityResult
from research_harness.watchdog.evaluator import WatchdogResult


class ScientificOutcome(BaseModel):
    """A valid scientific result — not an operational failure."""

    metric_name: str = "effect_size"
    metric_value: float
    hypothesis_supported: bool
    interpretation: str
    completed_units: int


@dataclass(frozen=True)
class ScientificRecordResult:
    recorded: bool
    outcome: ScientificOutcome | None = None
    reason: str | None = None


class ScientificResultRecorder:
    """Record negative or positive scientific outcomes when operations are healthy."""

    def __init__(
        self,
        *,
        project_id: str,
        contract_version: int,
        ledger: LedgerStore,
        effect_threshold: float = 0.05,
    ) -> None:
        self.project_id = project_id
        self.contract_version = contract_version
        self.ledger = ledger
        self.effect_threshold = effect_threshold

    def maybe_record(
        self,
        *,
        observed: ObservedState,
        watchdog: WatchdogResult,
        validity: ValidityResult,
        metric_value: float,
        metric_name: str = "effect_size",
        already_recorded: bool = False,
    ) -> ScientificRecordResult:
        if already_recorded:
            return ScientificRecordResult(recorded=False, reason="already_recorded")
        if watchdog.should_open_incident:
            return ScientificRecordResult(recorded=False, reason="operational_incident")
        if not validity.passed:
            return ScientificRecordResult(recorded=False, reason="validity_failed")
        if validity.should_block:
            return ScientificRecordResult(recorded=False, reason="validity_blocked")

        supported = abs(metric_value) >= self.effect_threshold
        interpretation = (
            "hypothesis_supported"
            if supported
            else "hypothesis_not_supported_negative_result"
        )
        outcome = ScientificOutcome(
            metric_name=metric_name,
            metric_value=metric_value,
            hypothesis_supported=supported,
            interpretation=interpretation,
            completed_units=observed.completed_units,
        )
        self.ledger.append(
            project_id=self.project_id,
            contract_version=self.contract_version,
            event_type=LedgerEventType.SCIENTIFIC_RESULT,
            payload=outcome.model_dump(mode="json"),
        )
        return ScientificRecordResult(recorded=True, outcome=outcome)
