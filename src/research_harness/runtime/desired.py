from __future__ import annotations

from research_harness.contract.models import ProjectContract
from research_harness.models.enums import Lifecycle
from research_harness.models.state import DesiredState


def build_desired_state(
    contract: ProjectContract,
    *,
    fingerprint_fields: dict[str, str],
    lifecycle: Lifecycle = Lifecycle.RUNNING,
) -> DesiredState:
    """Build desired state from contract and configured fingerprint field values."""
    ordered: dict[str, str] = {}
    for field in contract.fingerprint.fields:
        if field in fingerprint_fields:
            ordered[field] = fingerprint_fields[field]
    # Preserve any extra project-specific fields after declared ones.
    for key, value in fingerprint_fields.items():
        if key not in ordered:
            ordered[key] = value

    return DesiredState(
        project_id=contract.project.id,
        contract_version=contract.contract_version,
        lifecycle=lifecycle,
        fingerprint=ordered,
        completion_condition=contract.completion.condition,
    )
