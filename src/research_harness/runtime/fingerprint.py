from __future__ import annotations

from dataclasses import dataclass

from research_harness.contract.models import FingerprintConfig
from research_harness.models.enums import RuntimeFreshness

# Stronger actions win when multiple fingerprint fields change.
_ACTION_RANK: dict[str, int] = {
    "no_action": 0,
    "hot_reload": 1,
    "worker_restart": 2,
    "service_restart": 3,
    "rebuild": 4,
    "full_relaunch": 5,
}


@dataclass(frozen=True)
class FingerprintComparison:
    freshness: RuntimeFreshness
    changed_fields: list[str]
    desired: dict[str, str]
    observed: dict[str, str]


def compare_fingerprints(
    *,
    desired: dict[str, str],
    observed: dict[str, str],
    fields: list[str] | None = None,
) -> FingerprintComparison:
    """Compare desired vs observed fingerprint fields.

    When ``fields`` is provided, only those keys are compared (in order).
    Otherwise all keys present in ``desired`` are compared.
    """
    keys = fields if fields is not None else list(desired.keys())
    changed: list[str] = []
    for key in keys:
        desired_value = desired.get(key)
        observed_value = observed.get(key)
        if desired_value != observed_value:
            changed.append(key)

    freshness = RuntimeFreshness.CURRENT if not changed else RuntimeFreshness.STALE
    return FingerprintComparison(
        freshness=freshness,
        changed_fields=changed,
        desired=dict(desired),
        observed=dict(observed),
    )


def select_relaunch_action(
    config: FingerprintConfig,
    comparison: FingerprintComparison,
) -> str | None:
    """Pick the strongest relaunch action required by the deployment delta bundle."""
    if comparison.freshness == RuntimeFreshness.CURRENT:
        return None

    strongest = "no_action"
    strongest_rank = _ACTION_RANK[strongest]
    for field in comparison.changed_fields:
        action = config.action_for_field(field)
        rank = _ACTION_RANK.get(action, _ACTION_RANK[config.default])
        if rank > strongest_rank:
            strongest = action
            strongest_rank = rank
    return strongest


def deployment_delta_digest(changed_fields: list[str], *, desired: dict[str, str]) -> str:
    """Stable digest for a bundled deployment promotion."""
    parts = [f"{field}={desired[field]}" for field in sorted(changed_fields) if field in desired]
    return "|".join(parts)


def fingerprint_digest(fields: dict[str, str]) -> str:
    """Stable short digest for display (not cryptographic)."""
    parts = [f"{key}={fields[key]}" for key in sorted(fields)]
    return "|".join(parts)
