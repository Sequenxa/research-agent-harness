from __future__ import annotations

from research_harness.contract.models import ValidityCheck
from research_harness.contract.template import default_contract
from research_harness.models.enums import RuntimeFreshness
from research_harness.validity import evaluate_validity


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
