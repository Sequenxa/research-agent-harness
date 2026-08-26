from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from research_harness.contract.loader import load_contract, write_contract
from research_harness.contract.models import AllowListRule, Duration
from research_harness.contract.template import default_contract

FIXTURES = Path(__file__).parent / "fixtures"


def test_load_spec_example_contract() -> None:
    contract = load_contract(FIXTURES / "contract-v1.1.yaml")
    assert contract.contract_version == 1
    assert contract.project.id == "policy-simulation-eval"
    assert contract.fingerprint.action_for_field("model") == "full_relaunch"
    assert contract.fingerprint.action_for_field("prompt_version") == "worker_restart"
    assert contract.progress.watermarks[0].stall_after.seconds == 20 * 60


def test_default_contract_round_trip(tmp_path: Path) -> None:
    contract = default_contract(project_id="example", objective="Test objective.")
    path = tmp_path / "contract.yaml"
    write_contract(contract, path)
    reloaded = load_contract(path)
    assert reloaded.project.id == "example"
    assert reloaded.project.objective == "Test objective."
    assert reloaded.contract_version == 1


def test_invalid_contract_version_rejected(tmp_path: Path) -> None:
    data = yaml.safe_load((FIXTURES / "contract-v1.1.yaml").read_text(encoding="utf-8"))
    data["contract_version"] = 99
    path = tmp_path / "bad-contract.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")

    with pytest.raises(ValidationError):
        load_contract(path)


def test_invalid_duration_rejected() -> None:
    with pytest.raises(ValueError, match="Invalid duration"):
        Duration.parse("not-a-duration")


def test_model_swaps_allowlist_parsing() -> None:
    contract = load_contract(FIXTURES / "contract-v1.1.yaml")
    assert isinstance(contract.authority.model_swaps, AllowListRule)
    assert contract.authority.model_swaps.allow == ["gpt-4o-mini", "claude-haiku-4-5"]
