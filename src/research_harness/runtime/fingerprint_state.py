from __future__ import annotations

from dataclasses import dataclass

from research_harness.models.enums import RuntimeFreshness
from research_harness.runtime.fingerprint import compare_fingerprints


@dataclass(frozen=True)
class FingerprintState:
    """Three-way fingerprint view: running, desired deployment, and repository."""

    running: dict[str, str]
    desired: dict[str, str]
    repository: dict[str, str] | None = None

    def runtime_freshness(self, *, fields: list[str]) -> RuntimeFreshness:
        return compare_fingerprints(
            desired=self.desired,
            observed=self.running,
            fields=fields,
        ).freshness

    def repository_ahead_of_desired(self, *, fields: list[str]) -> bool:
        if self.repository is None:
            return False
        comparison = compare_fingerprints(
            desired=self.desired,
            observed=self.repository,
            fields=fields,
        )
        return comparison.freshness == RuntimeFreshness.STALE

    def reconciliation_required(self, *, fields: list[str]) -> bool:
        return self.runtime_freshness(fields=fields) == RuntimeFreshness.STALE

    def desired_explicitly_set(self, *, running_default: dict[str, str]) -> bool:
        return self.desired != running_default
