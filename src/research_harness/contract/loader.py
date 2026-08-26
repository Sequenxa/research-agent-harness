from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from research_harness.contract.models import ProjectContract


def load_contract(path: Path | str) -> ProjectContract:
    """Load and validate a project contract from a YAML file."""
    contract_path = Path(path)
    raw_text = contract_path.read_text(encoding="utf-8")
    data: Any = yaml.safe_load(raw_text)
    if not isinstance(data, dict):
        msg = f"Contract file must contain a YAML mapping: {contract_path}"
        raise ValueError(msg)
    return ProjectContract.model_validate(data)


def write_contract(contract: ProjectContract, path: Path | str) -> None:
    """Write a validated contract to a YAML file."""
    contract_path = Path(path)
    contract_path.parent.mkdir(parents=True, exist_ok=True)
    payload = contract.model_dump_yaml()
    contract_path.write_text(
        yaml.safe_dump(payload, sort_keys=False, default_flow_style=False),
        encoding="utf-8",
    )


def format_validation_error(error: ValidationError) -> str:
    lines = ["Contract validation failed:"]
    for item in error.errors():
        location = ".".join(str(part) for part in item["loc"])
        lines.append(f"  - {location}: {item['msg']}")
    return "\n".join(lines)
