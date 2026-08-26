from __future__ import annotations

from research_harness.contract.models import FingerprintConfig
from research_harness.models.enums import RuntimeFreshness
from research_harness.runtime.fingerprint import (
    FingerprintComparison,
    compare_fingerprints,
    select_relaunch_action,
)


def test_matching_fingerprints_are_current() -> None:
    desired = {"git_sha": "abc", "model": "m1", "config_hash": "c1"}
    observed = {"git_sha": "abc", "model": "m1", "config_hash": "c1"}
    result = compare_fingerprints(desired=desired, observed=observed)
    assert result.freshness == RuntimeFreshness.CURRENT
    assert result.changed_fields == []


def test_mismatched_fingerprints_are_stale() -> None:
    desired = {"git_sha": "abc", "model": "m2", "config_hash": "c1"}
    observed = {"git_sha": "abc", "model": "m1", "config_hash": "c1"}
    result = compare_fingerprints(desired=desired, observed=observed)
    assert result.freshness == RuntimeFreshness.STALE
    assert result.changed_fields == ["model"]


def test_missing_observed_field_counts_as_changed() -> None:
    desired = {"git_sha": "abc", "model": "m1"}
    observed = {"git_sha": "abc"}
    result = compare_fingerprints(desired=desired, observed=observed)
    assert result.freshness == RuntimeFreshness.STALE
    assert result.changed_fields == ["model"]


def test_select_relaunch_action_picks_strongest() -> None:
    config = FingerprintConfig(
        fields=["prompt_version", "lock_hash", "model"],
        on_change={
            "prompt_version": "worker_restart",
            "lock_hash": "rebuild",
            "model": "full_relaunch",
        },
        default="full_relaunch",
    )
    comparison = FingerprintComparison(
        freshness=RuntimeFreshness.STALE,
        changed_fields=["prompt_version", "lock_hash"],
        desired={"prompt_version": "v2", "lock_hash": "b"},
        observed={"prompt_version": "v1", "lock_hash": "a"},
    )
    assert select_relaunch_action(config, comparison) == "rebuild"


def test_select_relaunch_action_uses_default_for_unmapped_field() -> None:
    config = FingerprintConfig(
        fields=["custom_field"],
        on_change={},
        default="worker_restart",
    )
    comparison = FingerprintComparison(
        freshness=RuntimeFreshness.STALE,
        changed_fields=["custom_field"],
        desired={"custom_field": "new"},
        observed={"custom_field": "old"},
    )
    assert select_relaunch_action(config, comparison) == "worker_restart"
