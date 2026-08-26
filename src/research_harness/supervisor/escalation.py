from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from research_harness.contract.models import ProjectContract
from research_harness.ledger import LedgerEventType, LedgerStore


@dataclass
class EscalationState:
    blocked: bool
    reason: str
    blocked_at: datetime | None = None
    escalated: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "blocked": self.blocked,
            "reason": self.reason,
            "blocked_at": self.blocked_at.isoformat() if self.blocked_at else None,
            "escalated": self.escalated,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> EscalationState:
        blocked_at_raw = data.get("blocked_at")
        return cls(
            blocked=bool(data.get("blocked", False)),
            reason=str(data.get("reason", "")),
            blocked_at=(
                datetime.fromisoformat(str(blocked_at_raw))
                if blocked_at_raw
                else None
            ),
            escalated=bool(data.get("escalated", False)),
        )


class EscalationManager:
    """File-channel escalation for blocked runs (Section 15)."""

    def __init__(self, *, state_dir: Path, contract: ProjectContract) -> None:
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.contract = contract
        self.path = self.state_dir / "escalation.json"

    def load(self) -> EscalationState:
        if not self.path.exists():
            return EscalationState(blocked=False, reason="")
        data: object = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return EscalationState(blocked=False, reason="")
        return EscalationState.from_dict(data)

    def save(self, state: EscalationState) -> None:
        self.path.write_text(
            json.dumps(state.to_dict(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def mark_blocked(self, reason: str, *, now: datetime | None = None) -> EscalationState:
        state = EscalationState(
            blocked=True,
            reason=reason,
            blocked_at=now or datetime.now(UTC),
        )
        self.save(state)
        return state

    def clear(self) -> None:
        if self.path.exists():
            self.path.unlink()

    def check_timeout(
        self,
        *,
        now: datetime | None = None,
        ledger: LedgerStore | None = None,
    ) -> EscalationState | None:
        """Return updated state if escalation timeout triggered action."""
        state = self.load()
        if not state.blocked or state.blocked_at is None or state.escalated:
            return None

        evaluated_at = now or datetime.now(UTC)
        elapsed = (evaluated_at - state.blocked_at).total_seconds()
        timeout = self.contract.escalation.blocking_timeout.seconds
        if elapsed < timeout:
            return None

        state.escalated = True
        self.save(state)
        if ledger is not None:
            ledger.append(
                project_id=self.contract.project.id,
                contract_version=self.contract.contract_version,
                event_type=LedgerEventType.INCIDENT,
                payload={
                    "action": "escalation_timeout",
                    "on_timeout": self.contract.escalation.on_timeout,
                    "reason": state.reason,
                },
            )
        return state
