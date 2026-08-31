from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from research_harness.completion import evaluate_completion
from research_harness.contract.models import ExperimentConfig, ValidityCheck
from research_harness.contract.template import default_contract
from research_harness.experiment.plan import (
    compute_content_hash,
    load_experiment_plan,
)
from research_harness.models.enums import Lifecycle, RuntimeFreshness
from research_harness.models.state import ObservedState
from research_harness.validity import evaluate_validity


def _write_plan(path: Path, **overrides: object):
    import json

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
                "decision_rule": "Interpret interval estimate; do not auto-select.",
            }
        ],
    }
    payload.update(overrides)
    body = {k: v for k, v in payload.items() if k != "plan_hash"}
    payload["plan_hash"] = compute_content_hash(body)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return load_experiment_plan(path)


def test_validity_passes_when_rates_ok() -> None:
    contract = default_contract(project_id="demo", objective="obj")
    result = evaluate_validity(
        contract=contract,
        completed_units=100,
        null_units=1,
        error_units=2,
        runtime_freshness=RuntimeFreshness.CURRENT,
    )
    assert result.passed
    assert not result.should_open_incident


def test_validity_fails_on_high_error_rate() -> None:
    contract = default_contract(project_id="demo", objective="obj")
    result = evaluate_validity(
        contract=contract,
        completed_units=100,
        error_units=20,
        runtime_freshness=RuntimeFreshness.CURRENT,
    )
    assert not result.passed
    assert result.should_open_incident


def test_validity_block_on_custom_check() -> None:
    contract = default_contract(project_id="demo", objective="obj")
    contract.validity.checks.append(
        ValidityCheck(id="shard_count", adapter="dataset", on_fail="block")
    )
    result = evaluate_validity(
        contract=contract,
        completed_units=50,
        runtime_freshness=RuntimeFreshness.CURRENT,
        custom_results={"shard_count": False},
    )
    assert not result.passed
    assert result.should_block


def test_validity_without_plan_unchanged() -> None:
    contract = default_contract(project_id="demo", objective="obj")
    assert contract.experiment is None
    result = evaluate_validity(
        contract=contract,
        completed_units=10,
        runtime_freshness=RuntimeFreshness.CURRENT,
        plan=None,
    )
    assert result.passed


def test_validity_fails_on_schedule_hash_mismatch(tmp_path: Path) -> None:
    plan = _write_plan(tmp_path / "plan.json")
    contract = default_contract(project_id="demo", objective="obj")
    contract.validity.expected_units = 4
    contract.experiment = ExperimentConfig(plan="plan.json")
    result = evaluate_validity(
        contract=contract,
        completed_units=1,
        runtime_freshness=RuntimeFreshness.CURRENT,
        plan=plan,
        observed_schedule_hash="wrong-hash",
    )
    assert not result.passed
    assert any(c.check_id == "schedule_hash" for c in result.failed_checks)


def test_validity_blocks_unfrozen_plan_after_progress(tmp_path: Path) -> None:
    plan = _write_plan(tmp_path / "plan.json", frozen_before_outcomes=False)
    contract = default_contract(project_id="demo", objective="obj")
    contract.validity.expected_units = 4
    contract.experiment = ExperimentConfig(plan="plan.json")
    result = evaluate_validity(
        contract=contract,
        completed_units=1,
        runtime_freshness=RuntimeFreshness.CURRENT,
        plan=plan,
        observed_schedule_hash=plan.schedule_hash,
    )
    assert not result.passed
    assert result.should_block
    assert any(c.check_id == "plan_frozen" for c in result.failed_checks)


def test_completion_uses_planned_units(tmp_path: Path) -> None:
    plan = _write_plan(tmp_path / "plan.json", planned_units=4)
    contract = default_contract(project_id="demo", objective="obj")
    contract.validity.expected_units = 1000
    contract.completion.condition = "units_completed >= 1000 and validity.passed"
    contract.experiment = ExperimentConfig(plan="plan.json")
    observed = ObservedState(
        project_id="demo",
        observed_at=datetime.now(UTC),
        lifecycle=Lifecycle.RUNNING,
        completed_units=4,
        fingerprint={"plan_hash": plan.plan_hash},
    )
    validity = evaluate_validity(
        contract=contract,
        completed_units=4,
        runtime_freshness=RuntimeFreshness.CURRENT,
        plan=plan,
        observed_schedule_hash=plan.schedule_hash,
    )
    result = evaluate_completion(
        contract=contract, observed=observed, validity=validity, plan=plan
    )
    assert result.met


def test_completion_requires_frozen_plan(tmp_path: Path) -> None:
    plan = _write_plan(tmp_path / "plan.json", frozen_before_outcomes=False)
    contract = default_contract(project_id="demo", objective="obj")
    contract.validity.expected_units = 4
    contract.experiment = ExperimentConfig(plan="plan.json")
    observed = ObservedState(
        project_id="demo",
        observed_at=datetime.now(UTC),
        lifecycle=Lifecycle.RUNNING,
        completed_units=4,
    )
    validity = evaluate_validity(
        contract=contract,
        completed_units=0,
        runtime_freshness=RuntimeFreshness.CURRENT,
        plan=plan,
        observed_schedule_hash=plan.schedule_hash,
        require_frozen=False,
    )
    result = evaluate_completion(
        contract=contract, observed=observed, validity=validity, plan=plan
    )
    assert not result.met
    assert "not frozen" in result.reason
