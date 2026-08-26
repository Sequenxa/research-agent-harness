from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime

from research_harness.contract.models import ProjectContract

DEFAULT_STRATEGIES: tuple[str, ...] = (
    "worker_restart",
    "service_restart",
    "full_relaunch",
)


@dataclass(frozen=True)
class RecoveryDecision:
    allowed: bool
    strategy: str | None = None
    blocked_reason: str | None = None


@dataclass
class RecoveryAttempt:
    strategy: str
    evidence_digest: str
    recorded_at: datetime
    succeeded: bool = False


def stable_evidence(evidence: dict[str, object]) -> dict[str, object]:
    """Evidence fields that define the failure — exclude mutable recovery state."""
    excluded = {"recovery_attempts", "burn_in", "blocked", "checkpoint_resumed"}
    return {key: value for key, value in evidence.items() if key not in excluded}


def evidence_digest(evidence: dict[str, object]) -> str:
    payload = json.dumps(stable_evidence(evidence), sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def is_oscillating(strategies: list[str], window: int) -> bool:
    if len(strategies) < window:
        return False
    tail = strategies[-window:]
    if len(set(tail)) != 2:
        return False
    return all(tail[index] != tail[index + 1] for index in range(len(tail) - 1))


@dataclass
class RecoveryBudgetTracker:
    contract: ProjectContract
    attempts: list[RecoveryAttempt] = field(default_factory=list)

    def next_strategy(
        self,
        *,
        evidence: dict[str, object],
        incident_opened_at: datetime,
        now: datetime,
    ) -> RecoveryDecision:
        recovery = self.contract.recovery
        budget = self.contract.budget

        if len(self.attempts) >= recovery.max_attempts_per_incident:
            return RecoveryDecision(
                allowed=False,
                blocked_reason="max_attempts_per_incident exceeded",
            )

        if budget.per_incident_wallclock is not None:
            elapsed = (now - incident_opened_at).total_seconds()
            if elapsed > budget.per_incident_wallclock.seconds:
                return RecoveryDecision(
                    allowed=False,
                    blocked_reason="per_incident_wallclock exceeded",
                )

        digest = evidence_digest(evidence)
        strategies_tried = [attempt.strategy for attempt in self.attempts]
        if recovery.detect_oscillation and is_oscillating(
            strategies_tried, recovery.oscillation_window
        ):
            return RecoveryDecision(allowed=False, blocked_reason="oscillation detected")

        prior_digests = {attempt.evidence_digest for attempt in self.attempts}
        for strategy in DEFAULT_STRATEGIES:
            identical = sum(
                1
                for attempt in self.attempts
                if attempt.strategy == strategy and attempt.evidence_digest == digest
            )
            if identical >= recovery.max_identical_attempts:
                continue

            if (
                recovery.novel_strategy_requires == "evidence_delta"
                and strategy not in strategies_tried
                and prior_digests
                and digest in prior_digests
            ):
                any_exhausted = any(
                    sum(
                        1
                        for attempt in self.attempts
                        if attempt.strategy == tried and attempt.evidence_digest == digest
                    )
                    >= recovery.max_identical_attempts
                    for tried in set(strategies_tried)
                )
                if not any_exhausted:
                    continue

            return RecoveryDecision(allowed=True, strategy=strategy)

        return RecoveryDecision(
            allowed=False,
            blocked_reason="no authorized remediation remains",
        )

    def record_attempt(
        self,
        *,
        strategy: str,
        evidence: dict[str, object],
        now: datetime,
        succeeded: bool,
    ) -> None:
        self.attempts.append(
            RecoveryAttempt(
                strategy=strategy,
                evidence_digest=evidence_digest(evidence),
                recorded_at=now,
                succeeded=succeeded,
            )
        )

    def to_evidence(self) -> list[dict[str, object]]:
        return [
            {
                "strategy": attempt.strategy,
                "evidence_digest": attempt.evidence_digest,
                "recorded_at": attempt.recorded_at.isoformat(),
                "succeeded": attempt.succeeded,
            }
            for attempt in self.attempts
        ]

    @classmethod
    def from_evidence(
        cls, contract: ProjectContract, raw: list[dict[str, object]]
    ) -> RecoveryBudgetTracker:
        tracker = cls(contract=contract)
        for item in raw:
            tracker.attempts.append(
                RecoveryAttempt(
                    strategy=str(item["strategy"]),
                    evidence_digest=str(item["evidence_digest"]),
                    recorded_at=datetime.fromisoformat(str(item["recorded_at"])),
                    succeeded=bool(item.get("succeeded", False)),
                )
            )
        return tracker
