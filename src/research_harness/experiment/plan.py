from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator


class ConfirmatoryAnalysis(BaseModel):
    """Prespecified analysis decision rule — text only; never auto-scored."""

    analysis_id: str = Field(min_length=1)
    decision_rule: str = Field(min_length=1)


class ExperimentPlan(BaseModel):
    """Thin projection of methodology artifacts for harness reconciliation.

    Produced by agents (or hand-written) from experimental-design / power /
    hypothesis-generation outputs. The harness reads this file; it does not
    invent scientific intent.
    """

    schema_version: str = "1.0"
    planned_units: int = Field(ge=1)
    design_seed: int | str | None = None
    unit_of_analysis: str = Field(min_length=1)
    frozen_before_outcomes: bool = False
    schedule_path: str | None = None
    schedule_hash: str | None = None
    plan_hash: str = Field(min_length=1)
    confirmatory_analyses: list[ConfirmatoryAnalysis] = Field(default_factory=list)

    @field_validator("schema_version")
    @classmethod
    def validate_schema_version(cls, value: str) -> str:
        if value != "1.0":
            msg = f"Unsupported experiment plan schema_version {value!r}; only 1.0"
            raise ValueError(msg)
        return value


def compute_content_hash(payload: dict[str, Any]) -> str:
    """Stable SHA-256 over canonical JSON (sorted keys, no plan_hash field)."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def plan_body_for_hash(plan: ExperimentPlan | dict[str, Any]) -> dict[str, Any]:
    """Serialize plan fields that participate in ``plan_hash``."""
    data = plan.model_dump(mode="json") if isinstance(plan, ExperimentPlan) else dict(plan)
    data.pop("plan_hash", None)
    return data


def compute_plan_hash(plan: ExperimentPlan | dict[str, Any]) -> str:
    return compute_content_hash(plan_body_for_hash(plan))


def load_experiment_plan(path: Path | str) -> ExperimentPlan:
    """Load and validate an experiment plan JSON file."""
    plan_path = Path(path)
    raw = json.loads(plan_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        msg = f"Experiment plan must be a JSON object: {plan_path}"
        raise ValueError(msg)
    return ExperimentPlan.model_validate(raw)


def hash_schedule_file(path: Path | str) -> str:
    """SHA-256 of schedule file bytes (for schedule_hash checks)."""
    data = Path(path).read_bytes()
    return hashlib.sha256(data).hexdigest()


def count_schedule_rows(path: Path | str) -> int:
    """Count non-empty data rows in a CSV schedule (header excluded)."""
    text = Path(path).read_text(encoding="utf-8")
    rows = [line for line in text.splitlines() if line.strip()]
    if not rows:
        return 0
    # Assume first row is header when it looks like a CSV header.
    return max(len(rows) - 1, 0)


def resolve_expected_units(
    *,
    contract: Any,
    plan: ExperimentPlan | None,
) -> int:
    """Prefer plan.planned_units when a plan is loaded; else contract.validity."""
    if plan is not None:
        return plan.planned_units
    return int(contract.validity.expected_units)


def validate_plan_against_contract(
    *,
    plan: ExperimentPlan,
    expected_units: int,
    observed_schedule_hash: str | None = None,
    require_frozen: bool = False,
) -> None:
    """Raise ValueError when plan integrity or freeze gates fail."""
    computed = compute_plan_hash(plan)
    if plan.plan_hash != computed:
        msg = (
            f"plan_hash mismatch: recorded={plan.plan_hash!r} computed={computed!r}"
        )
        raise ValueError(msg)

    if plan.planned_units != expected_units:
        msg = (
            f"planned_units ({plan.planned_units}) != expected_units ({expected_units})"
        )
        raise ValueError(msg)

    if (
        observed_schedule_hash is not None
        and plan.schedule_hash is not None
        and observed_schedule_hash != plan.schedule_hash
    ):
        msg = (
            f"schedule_hash mismatch: plan={plan.schedule_hash!r} "
            f"observed={observed_schedule_hash!r}"
        )
        raise ValueError(msg)

    if require_frozen and not plan.frozen_before_outcomes:
        msg = "experiment plan must be frozen_before_outcomes before recording results"
        raise ValueError(msg)


def resolve_plan_path(contract: Any, *, base_dir: Path | None = None) -> Path | None:
    """Return absolute plan path from contract.experiment, or None if absent."""
    experiment = getattr(contract, "experiment", None)
    if experiment is None or not experiment.plan:
        return None
    path = Path(experiment.plan)
    if not path.is_absolute() and base_dir is not None:
        path = base_dir / path
    return path


def load_plan_for_contract(
    contract: Any,
    *,
    base_dir: Path | None = None,
) -> ExperimentPlan | None:
    """Load experiment plan when contract.experiment is set; else None.

    Relative paths resolve against ``base_dir`` (typically the project root
    beside ``contract.yaml``, or ``state_dir.parent``).
    """
    path = resolve_plan_path(contract, base_dir=base_dir)
    if path is None:
        return None
    if not path.exists():
        msg = f"Experiment plan not found: {path}"
        raise FileNotFoundError(msg)
    return load_experiment_plan(path)


def resolve_schedule_hash_for_contract(
    contract: Any,
    *,
    plan: ExperimentPlan | None,
    base_dir: Path | None = None,
) -> str | None:
    """Hash the schedule file when contract or plan points at one."""
    schedule: str | None = None
    experiment = getattr(contract, "experiment", None)
    if experiment is not None and experiment.schedule:
        schedule = experiment.schedule
    elif plan is not None and plan.schedule_path:
        schedule = plan.schedule_path
    if not schedule:
        return None
    path = Path(schedule)
    if not path.is_absolute() and base_dir is not None:
        path = base_dir / path
    if not path.exists():
        return None
    return hash_schedule_file(path)
