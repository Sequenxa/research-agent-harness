from __future__ import annotations

from datetime import UTC, datetime, timedelta

from research_harness.contract.models import Duration
from research_harness.contract.template import default_contract
from research_harness.models.enums import Health, Progress
from research_harness.models.state import ObservedState
from research_harness.watchdog import ProgressContext, WatermarkObservation, evaluate_watchdog


def _fast_contract():
    contract = default_contract(project_id="demo", objective="obj")
    for watermark in contract.progress.watermarks:
        watermark.stall_after = Duration.parse("1s")
    return contract


def test_healthy_advancing_worker_not_stalled() -> None:
    contract = _fast_contract()
    now = datetime.now(UTC)
    observed = ObservedState(
        project_id="demo",
        observed_at=now,
        health=Health.HEALTHY,
        progress=Progress.ADVANCING,
        completed_units=5,
        last_progress_at=now,
    )
    progress = ProgressContext(
        watermarks={
            "completed_units": WatermarkObservation("completed_units", 5, now),
            "worker_heartbeat": WatermarkObservation("worker_heartbeat", 1, now),
        }
    )
    result = evaluate_watchdog(
        contract=contract, observed=observed, progress=progress, now=now
    )
    assert result.health == Health.HEALTHY
    assert result.progress == Progress.ADVANCING
    assert not result.should_open_incident


def test_alive_but_stalled_watermark_opens_incident() -> None:
    contract = _fast_contract()
    now = datetime.now(UTC)
    stale = now - timedelta(seconds=30)
    observed = ObservedState(
        project_id="demo",
        observed_at=now,
        health=Health.HEALTHY,
        progress=Progress.ADVANCING,
        completed_units=5,
        last_progress_at=stale,
    )
    progress = ProgressContext(
        watermarks={
            "completed_units": WatermarkObservation("completed_units", 5, stale),
            "worker_heartbeat": WatermarkObservation("worker_heartbeat", 1, stale),
        }
    )
    result = evaluate_watchdog(
        contract=contract, observed=observed, progress=progress, now=now
    )
    assert result.health == Health.HEALTHY
    assert result.progress == Progress.STALLED
    assert result.symptom == "progress_stalled"
    assert result.should_open_incident


def test_unhealthy_worker_opens_incident() -> None:
    contract = _fast_contract()
    now = datetime.now(UTC)
    observed = ObservedState(
        project_id="demo",
        observed_at=now,
        health=Health.UNHEALTHY,
        progress=Progress.STALLED,
        completed_units=0,
    )
    result = evaluate_watchdog(
        contract=contract,
        observed=observed,
        progress=ProgressContext(),
        now=now,
    )
    assert result.symptom == "worker_unhealthy"
    assert result.should_open_incident
