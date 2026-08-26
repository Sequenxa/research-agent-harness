from __future__ import annotations

from datetime import UTC, datetime, timedelta

from research_harness.contract.models import Duration
from research_harness.contract.template import default_contract
from research_harness.models.enums import Health, Progress, VerificationLevel
from research_harness.models.state import ObservedState
from research_harness.verification import BurnInState, evaluate_burn_in


def test_burn_in_requires_units_and_duration() -> None:
    contract = default_contract(project_id="demo", objective="obj")
    contract.verification.stable_after.units = 3
    contract.verification.stable_after.min_duration = Duration.parse("10s")
    now = datetime.now(UTC)
    state = BurnInState()
    observed = ObservedState(
        project_id="demo",
        observed_at=now,
        health=Health.HEALTHY,
        progress=Progress.ADVANCING,
        completed_units=2,
    )
    state = evaluate_burn_in(
        contract=contract,
        observed=observed,
        health_ok=True,
        progress_ok=True,
        state=state,
        now=now,
        patched=True,
    )
    assert state.level == VerificationLevel.VERIFIED

    observed.completed_units = 5
    state = evaluate_burn_in(
        contract=contract,
        observed=observed,
        health_ok=True,
        progress_ok=True,
        state=state,
        now=now + timedelta(seconds=5),
        patched=True,
    )
    assert state.level == VerificationLevel.VERIFIED

    state = evaluate_burn_in(
        contract=contract,
        observed=observed,
        health_ok=True,
        progress_ok=True,
        state=state,
        now=now + timedelta(seconds=11),
        patched=True,
    )
    assert state.level == VerificationLevel.STABLE
