from __future__ import annotations

from research_harness.adapters.base import RuntimeAdapter
from research_harness.contract.models import ProjectContract
from research_harness.models.enums import FingerprintFieldClass, Lifecycle
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


def merge_repository_deployment_fields(
    runtime: RuntimeAdapter,
    desired: dict[str, str],
) -> tuple[dict[str, str], list[str]]:
    """Merge repository deployment fields into desired after prerequisite repairs.

    Research-semantic fields stay on the explicit desired deployment intent.
    """
    getter = getattr(runtime, "repository_fingerprint", None)
    if not callable(getter):
        return dict(desired), []
    repository = getter()
    if not repository:
        return dict(desired), []
    classifications = runtime.fingerprint_field_classifications()
    merged = dict(desired)
    synced_fields: list[str] = []
    for field, value in dict(repository).items():
        field_class = classifications.get(field, FingerprintFieldClass.DEPLOYMENT.value)
        if field_class == FingerprintFieldClass.RESEARCH_SEMANTIC.value:
            continue
        if merged.get(field) != value:
            merged[field] = value
            synced_fields.append(field)
    return merged, synced_fields
