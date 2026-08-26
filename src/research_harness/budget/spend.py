from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from research_harness.contract.models import ProjectContract


@dataclass
class BudgetState:
    spend_usd: float = 0.0


class BudgetTracker:
    """Persist nominal spend and enforce ceilings before actions."""

    def __init__(self, *, state_dir: Path, contract: ProjectContract) -> None:
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.contract = contract
        self.path = self.state_dir / "budget.json"
        self.state = self._load()

    def _load(self) -> BudgetState:
        if not self.path.exists():
            return BudgetState()
        data: object = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return BudgetState()
        return BudgetState(spend_usd=float(data.get("spend_usd", 0.0)))

    def persist(self) -> None:
        self.path.write_text(
            json.dumps({"spend_usd": self.state.spend_usd}, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def can_spend(self, amount: float) -> tuple[bool, str | None]:
        if amount < 0:
            return False, "budget: negative spend amount"
        ceiling = self.contract.budget.total_usd
        if self.state.spend_usd + amount > ceiling:
            return False, (
                f"budget.total_usd exceeded ({self.state.spend_usd + amount:.2f} > {ceiling})"
            )
        return True, None

    def record_spend(self, amount: float) -> float:
        self.state.spend_usd += amount
        self.persist()
        return self.state.spend_usd
