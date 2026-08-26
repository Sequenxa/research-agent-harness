from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from research_harness.contract.models import ProjectContract
from research_harness.models.enums import Health, Progress, VerificationLevel
from research_harness.models.state import ObservedState


@dataclass
class BurnInState:
    level: VerificationLevel | None = None
    patched_at: datetime | None = None
    verified_at: datetime | None = None
    stable_at: datetime | None = None
    units_at_verified: int = 0

    def to_dict(self) -> dict[str, object]:
        return {
            "level": self.level.value if self.level is not None else None,
            "patched_at": self.patched_at.isoformat() if self.patched_at else None,
            "verified_at": self.verified_at.isoformat() if self.verified_at else None,
            "stable_at": self.stable_at.isoformat() if self.stable_at else None,
            "units_at_verified": self.units_at_verified,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, object]) -> BurnInState:
        level_raw = raw.get("level")
        return cls(
            level=VerificationLevel(str(level_raw)) if level_raw else None,
            patched_at=(
                datetime.fromisoformat(str(raw["patched_at"]))
                if raw.get("patched_at")
                else None
            ),
            verified_at=(
                datetime.fromisoformat(str(raw["verified_at"]))
                if raw.get("verified_at")
                else None
            ),
            stable_at=(
                datetime.fromisoformat(str(raw["stable_at"]))
                if raw.get("stable_at")
                else None
            ),
            units_at_verified=int(str(raw.get("units_at_verified", 0))),
        )


def evaluate_burn_in(
    *,
    contract: ProjectContract,
    observed: ObservedState,
    health_ok: bool,
    progress_ok: bool,
    state: BurnInState,
    now: datetime,
    patched: bool,
) -> BurnInState:
    """Advance PATCHED → VERIFIED → STABLE using contract stable_after (AND semantics)."""
    updated = BurnInState(
        level=state.level,
        patched_at=state.patched_at,
        verified_at=state.verified_at,
        stable_at=state.stable_at,
        units_at_verified=state.units_at_verified,
    )

    if patched and updated.level is None:
        updated.level = VerificationLevel.PATCHED
        updated.patched_at = now

    if updated.level == VerificationLevel.PATCHED and health_ok and progress_ok:
        updated.level = VerificationLevel.VERIFIED
        updated.verified_at = now
        updated.units_at_verified = observed.completed_units

    if updated.level == VerificationLevel.VERIFIED and updated.verified_at is not None:
        units_delta = observed.completed_units - updated.units_at_verified
        duration = (now - updated.verified_at).total_seconds()
        stable = contract.verification.stable_after
        if units_delta >= stable.units and duration >= stable.min_duration.seconds:
            updated.level = VerificationLevel.STABLE
            updated.stable_at = now

    return updated


def health_ok(observed: ObservedState) -> bool:
    return observed.health == Health.HEALTHY


def progress_ok(watchdog_progress: Progress) -> bool:
    return watchdog_progress == Progress.ADVANCING
