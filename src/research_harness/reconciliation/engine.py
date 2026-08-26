from __future__ import annotations

from datetime import UTC, datetime

from research_harness.adapters.base import RuntimeAdapter
from research_harness.contract.models import ProjectContract
from research_harness.ledger import LedgerEventType, LedgerStore
from research_harness.models.enums import RuntimeFreshness
from research_harness.models.state import (
    DesiredState,
    ObservedState,
    ReconciliationDifference,
    ReconciliationResult,
)
from research_harness.runtime.desired import build_desired_state
from research_harness.runtime.fingerprint import (
    compare_fingerprints,
    select_relaunch_action,
)


class Reconciler:
    """Compare desired vs observed state and apply graded relaunch actions."""

    def __init__(
        self,
        *,
        contract: ProjectContract,
        runtime: RuntimeAdapter,
        ledger: LedgerStore | None = None,
    ) -> None:
        self.contract = contract
        self.runtime = runtime
        self.ledger = ledger

    def reconcile(self, *, desired_fingerprint: dict[str, str]) -> ReconciliationResult:
        desired = build_desired_state(self.contract, fingerprint_fields=desired_fingerprint)
        observed = self.runtime.inspect()
        return self._reconcile(desired=desired, observed=observed)

    def _reconcile(
        self,
        *,
        desired: DesiredState,
        observed: ObservedState,
    ) -> ReconciliationResult:
        comparison = compare_fingerprints(
            desired=desired.fingerprint,
            observed=observed.fingerprint,
            fields=self.contract.fingerprint.fields,
        )
        differences = [
            ReconciliationDifference(
                field=f"fingerprint.{field}",
                desired=desired.fingerprint.get(field),
                observed=observed.fingerprint.get(field),
                action=self.contract.fingerprint.action_for_field(field),
            )
            for field in comparison.changed_fields
        ]

        result = ReconciliationResult(
            project_id=desired.project_id,
            reconciled_at=datetime.now(UTC),
            differences=differences,
            actions_taken=[],
            success=True,
            blocked_reason=None,
        )

        if comparison.freshness == RuntimeFreshness.CURRENT:
            self._record(result, desired=desired, observed=observed, action=None)
            return result

        action = select_relaunch_action(self.contract.fingerprint, comparison)
        if action is None or action == "no_action":
            self._record(result, desired=desired, observed=observed, action=action)
            return result

        if not self.contract.authority.runtime_restarts:
            result.success = False
            result.blocked_reason = (
                "Fingerprint stale but authority.runtime_restarts is false; "
                f"required action={action}, changed={comparison.changed_fields}"
            )
            self._record(result, desired=desired, observed=observed, action=None)
            return result

        self.runtime.relaunch(action)
        result.actions_taken.append(action)

        # Re-inspect to confirm runtime adopted desired fingerprint.
        post = self.runtime.inspect()
        post_comparison = compare_fingerprints(
            desired=desired.fingerprint,
            observed=post.fingerprint,
            fields=self.contract.fingerprint.fields,
        )
        if post_comparison.freshness != RuntimeFreshness.CURRENT:
            result.success = False
            result.blocked_reason = (
                "Relaunch applied but observed fingerprint still stale: "
                f"{post_comparison.changed_fields}"
            )

        self._record(result, desired=desired, observed=post, action=action)
        return result

    def _record(
        self,
        result: ReconciliationResult,
        *,
        desired: DesiredState,
        observed: ObservedState,
        action: str | None,
    ) -> None:
        if self.ledger is None:
            return
        self.ledger.append(
            project_id=result.project_id,
            contract_version=desired.contract_version,
            event_type=LedgerEventType.RUNTIME_RECONCILIATION,
            payload={
                "success": result.success,
                "blocked_reason": result.blocked_reason,
                "action": action,
                "actions_taken": result.actions_taken,
                "differences": [d.model_dump(mode="json") for d in result.differences],
                "desired_fingerprint": desired.fingerprint,
                "observed_fingerprint": observed.fingerprint,
                "runtime_freshness": (
                    RuntimeFreshness.CURRENT.value
                    if not result.differences or result.success
                    else RuntimeFreshness.STALE.value
                ),
            },
        )
