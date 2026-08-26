from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from research_harness.contract.models import ProjectContract
from research_harness.models.enums import Health, Progress
from research_harness.models.state import ObservedState


@dataclass(frozen=True)
class WatermarkObservation:
    name: str
    value: float | int | None
    last_advanced_at: datetime | None


@dataclass
class ProgressContext:
    """Signals used by the watchdog beyond base observed state."""

    watermarks: dict[str, WatermarkObservation] = field(default_factory=dict)
    current_phase: str | None = None
    phase_started_at: datetime | None = None
    slow_operation_until: datetime | None = None


@dataclass(frozen=True)
class WatchdogResult:
    health: Health
    progress: Progress
    stalled_watermarks: tuple[str, ...]
    stalled_phases: tuple[str, ...]
    symptom: str | None
    should_open_incident: bool


def _seconds_since(moment: datetime | None, now: datetime) -> float | None:
    if moment is None:
        return None
    return (now - moment).total_seconds()


def _is_stalled(*, last_at: datetime | None, stall_after_seconds: float, now: datetime) -> bool:
    elapsed = _seconds_since(last_at, now)
    if elapsed is None:
        return True
    return elapsed > stall_after_seconds


def evaluate_watchdog(
    *,
    contract: ProjectContract,
    observed: ObservedState,
    progress: ProgressContext,
    now: datetime | None = None,
) -> WatchdogResult:
    """Evaluate health and hierarchical progress stalls."""
    evaluated_at = now or datetime.now(UTC)
    health = observed.health

    if progress.slow_operation_until is not None and evaluated_at < progress.slow_operation_until:
        return WatchdogResult(
            health=health,
            progress=Progress.ADVANCING,
            stalled_watermarks=(),
            stalled_phases=(),
            symptom=None,
            should_open_incident=False,
        )

    stalled_watermarks: list[str] = []
    for watermark in contract.progress.watermarks:
        signal = progress.watermarks.get(watermark.name)
        if signal is None:
            if watermark.source == "ledger":
                signal = WatermarkObservation(
                    name=watermark.name,
                    value=observed.completed_units,
                    last_advanced_at=observed.last_progress_at,
                )
            else:
                signal = WatermarkObservation(
                    name=watermark.name,
                    value=None,
                    last_advanced_at=observed.extra.get(f"{watermark.name}_last_at"),
                )
        if _is_stalled(
            last_at=signal.last_advanced_at,
            stall_after_seconds=watermark.stall_after.seconds,
            now=evaluated_at,
        ):
            stalled_watermarks.append(watermark.name)

    stalled_phases: list[str] = []
    if progress.current_phase is not None:
        phase = contract.progress.phases.get(progress.current_phase)
        if phase is not None and _is_stalled(
            last_at=progress.phase_started_at,
            stall_after_seconds=phase.stall_after.seconds,
            now=evaluated_at,
        ):
            stalled_phases.append(progress.current_phase)

    progress_stalled = False
    if contract.progress.stall_requires == "all":
        progress_stalled = bool(stalled_watermarks) and len(stalled_watermarks) == len(
            contract.progress.watermarks
        )
    else:
        progress_stalled = bool(stalled_watermarks)

    progress_status = Progress.STALLED if progress_stalled else Progress.ADVANCING

    symptom: str | None = None
    should_open_incident = False
    if health == Health.UNHEALTHY:
        symptom = "worker_unhealthy"
        should_open_incident = True
    elif progress_stalled:
        symptom = "progress_stalled"
        should_open_incident = True

    return WatchdogResult(
        health=health,
        progress=progress_status,
        stalled_watermarks=tuple(stalled_watermarks),
        stalled_phases=tuple(stalled_phases),
        symptom=symptom,
        should_open_incident=should_open_incident,
    )
