from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from research_harness.contract.loader import load_contract
from research_harness.contract.template import default_contract
from research_harness.experiment.plan import (
    ExperimentPlan,
    compute_content_hash,
    load_experiment_plan,
    resolve_expected_units,
    validate_plan_against_contract,
)


def _write_plan(path: Path, **overrides: object) -> ExperimentPlan:
    payload = {
        "schema_version": "1.0",
        "planned_units": 4,
        "design_seed": 42,
        "unit_of_analysis": "independent run cell",
        "frozen_before_outcomes": True,
        "schedule_path": "experiment/schedule.csv",
        "schedule_hash": "abc",
        "plan_hash": "",
        "confirmatory_analyses": [
            {
                "analysis_id": "A1",
                "decision_rule": "Interpret interval estimate; do not auto-select a hypothesis.",
            }
        ],
    }
    payload.update(overrides)
    # Compute plan_hash over canonical body excluding plan_hash itself.
    body = {k: v for k, v in payload.items() if k != "plan_hash"}
    payload["plan_hash"] = compute_content_hash(body)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return ExperimentPlan.model_validate(payload)


def test_load_experiment_plan_round_trip(tmp_path: Path) -> None:
    plan_path = tmp_path / "experiment" / "plan.json"
    written = _write_plan(plan_path)
    loaded = load_experiment_plan(plan_path)
    assert loaded.planned_units == 4
    assert loaded.frozen_before_outcomes is True
    assert loaded.plan_hash == written.plan_hash
    assert loaded.confirmatory_analyses[0].analysis_id == "A1"


def test_contract_accepts_optional_experiment_pointer(tmp_path: Path) -> None:
    contract = default_contract(project_id="demo", objective="obj")
    data = contract.model_dump_yaml()
    data["experiment"] = {
        "plan": "experiment/plan.json",
        "schedule": "experiment/schedule.csv",
    }
    path = tmp_path / "contract.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    loaded = load_contract(path)
    assert loaded.experiment is not None
    assert loaded.experiment.plan == "experiment/plan.json"
    assert loaded.experiment.schedule == "experiment/schedule.csv"


def test_contract_without_experiment_unchanged() -> None:
    contract = default_contract(project_id="demo", objective="obj")
    assert contract.experiment is None


def test_resolve_expected_units_from_plan(tmp_path: Path) -> None:
    from research_harness.contract.models import ExperimentConfig

    plan_path = tmp_path / "plan.json"
    _write_plan(plan_path, planned_units=8)
    contract = default_contract(project_id="demo", objective="obj")
    contract.validity.expected_units = 1000
    contract.experiment = ExperimentConfig(plan="plan.json")
    assert resolve_expected_units(contract=contract, plan=load_experiment_plan(plan_path)) == 8


def test_validate_plan_rejects_hash_mismatch(tmp_path: Path) -> None:
    plan_path = tmp_path / "plan.json"
    plan = _write_plan(plan_path)
    tampered = plan.model_copy(update={"plan_hash": "deadbeef"})
    with pytest.raises(ValueError, match="plan_hash"):
        validate_plan_against_contract(
            plan=tampered,
            expected_units=4,
            observed_schedule_hash=plan.schedule_hash,
        )


def test_validate_plan_rejects_schedule_hash_mismatch(tmp_path: Path) -> None:
    plan_path = tmp_path / "plan.json"
    plan = _write_plan(plan_path)
    with pytest.raises(ValueError, match="schedule_hash"):
        validate_plan_against_contract(
            plan=plan,
            expected_units=4,
            observed_schedule_hash="different",
        )


def test_validate_plan_rejects_unfrozen_when_recording(tmp_path: Path) -> None:
    plan_path = tmp_path / "plan.json"
    plan = _write_plan(plan_path, frozen_before_outcomes=False)
    with pytest.raises(ValueError, match="frozen"):
        validate_plan_against_contract(
            plan=plan,
            expected_units=4,
            observed_schedule_hash=plan.schedule_hash,
            require_frozen=True,
        )
