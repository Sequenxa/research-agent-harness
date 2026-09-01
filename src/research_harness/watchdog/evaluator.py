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
    scheduled_path_armed: bool | None = None
    operational_mode: str | None = None


@dataclass(frozen=True)
class WatchdogResult:
    health: Health
    progress: Progress
    stalled_watermarks: tuple[str, ...]
    stalled_phases: tuple[str, ...]
    symptom: str | None
    should_open_incident: bool
    suspect_watermarks: tuple[str, ...] = ()


def _seconds_since(moment: datetime | None, now: datetime) -> float | None:
    if moment is None:
        return None
    return (now - moment).total_seconds()


def _is_stalled(*, last_at: datetime | None, stall_after_seconds: float, now: datetime) -> bool:
    elapsed = _seconds_since(last_at, now)
    if elapsed is None:
        return True
    return elapsed > stall_after_seconds


def _is_suspect_progress_timestamp(
    *,
    last_at: datetime | None,
    observed_at: datetime,
    tolerance_seconds: float,
) -> bool:
    """True when last_advanced_at is not a durable watermark (≈ inspect time).

    Adapters that stamp ``last_progress_at = datetime.now()`` on every inspect
    make ``stall_after`` impossible to fire. Exact equality (tolerance 0) is the
    common lying pattern of assigning the same ``now`` to both fields.
    """
    if last_at is None:
        return False
    age = (observed_at - last_at).total_seconds()
    return age <= tolerance_seconds


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
    observe_clock = observed.observed_at
    tolerance = contract.progress.suspect_progress_within.seconds

    if progress.slow_operation_until is not None and evaluated_at < progress.slow_operation_until:
        return WatchdogResult(
            health=health,
            progress=Progress.ADVANCING,
            stalled_watermarks=(),
            stalled_phases=(),
            symptom=None,
            should_open_incident=False,
            suspect_watermarks=(),
        )

    stalled_watermarks: list[str] = []
    suspect_watermarks: list[str] = []
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
        if _is_suspect_progress_timestamp(
            last_at=signal.last_advanced_at,
            observed_at=observe_clock,
            tolerance_seconds=tolerance,
        ):
            suspect_watermarks.append(watermark.name)
            stalled_watermarks.append(watermark.name)
            continue
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

    drain_held = _scheduled_path_held_by_drain(
        contract=contract,
        progress=progress,
        progress_stalled=progress_stalled,
        evaluated_at=evaluated_at,
        observe_clock=observe_clock,
        observed=observed,
    )

    progress_status = (
        Progress.STALLED if progress_stalled or drain_held else Progress.ADVANCING
    )

    symptom: str | None = None
    should_open_incident = False
    if health == Health.UNHEALTHY:
        symptom = "worker_unhealthy"
        should_open_incident = True
    elif drain_held:
        symptom = "scheduled_path_held_by_drain"
        should_open_incident = True
    elif suspect_watermarks and progress_stalled:
        symptom = "suspect_progress"
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
        suspect_watermarks=tuple(suspect_watermarks),
    )


def _scheduled_path_held_by_drain(
    *,
    contract: ProjectContract,
    progress: ProgressContext,
    progress_stalled: bool,
    evaluated_at: datetime,
    observe_clock: datetime,
    observed: ObservedState,
) -> bool:
    """Escalate when recovery/drain leaves the scheduled path disarmed too long."""
    stall_after = contract.progress.scheduled_path_disarmed_stall_after
    if stall_after is None:
        return False

    armed = progress.scheduled_path_armed
    if armed is None:
        armed = observed.scheduled_path_armed
    if armed is not False:
        return False

    mode = progress.operational_mode or progress.current_phase
    if mode is None:
        extra_mode = observed.extra.get("operational_mode") or observed.extra.get("mode")
        mode = str(extra_mode) if extra_mode is not None else None
    if mode is None:
        return False

    drain_modes = {item.casefold() for item in contract.progress.drain_modes}
    if mode.casefold() not in drain_modes:
        return False

    if not progress_stalled:
        return False

    # Require the disarm condition to have persisted at least stall_after.
    # Prefer an explicit adapter timestamp; otherwise use the oldest watermark.
    disarmed_at = observed.extra.get("scheduled_path_disarmed_at")
    if isinstance(disarmed_at, datetime):
        return _is_stalled(
            last_at=disarmed_at,
            stall_after_seconds=stall_after.seconds,
            now=evaluated_at,
        )
    if isinstance(disarmed_at, str):
        try:
            parsed = datetime.fromisoformat(disarmed_at)
        except ValueError:
            parsed = None
        if parsed is not None:
            return _is_stalled(
                last_at=parsed,
                stall_after_seconds=stall_after.seconds,
                now=evaluated_at,
            )

    # Fallback: watermarks already stalled; require age beyond the drain threshold
    # using last_progress_at / observe clock as the disarm proxy.
    reference = observed.last_progress_at
    if reference is None:
        reference = observe_clock
    return _is_stalled(
        last_at=reference,
        stall_after_seconds=stall_after.seconds,
        now=evaluated_at,
    )
