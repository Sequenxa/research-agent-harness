from __future__ import annotations

from datetime import UTC, datetime

from research_harness.adapters.base import RuntimeAdapter
from research_harness.budget import BudgetTracker
from research_harness.contract.models import ProjectContract
from research_harness.ledger import LedgerEventType, LedgerStore
from research_harness.models.enums import MutationReadinessStatus, RuntimeFreshness
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
from research_harness.runtime.mutation import mutation_preflight_for


class Reconciler:
    """Compare desired vs observed state and apply graded relaunch actions."""

    def __init__(
        self,
        *,
        contract: ProjectContract,
        runtime: RuntimeAdapter,
        ledger: LedgerStore | None = None,
        observe_only: bool = False,
        budget_tracker: BudgetTracker | None = None,
        action_cost_usd: float = 0.10,
    ) -> None:
        self.contract = contract
        self.runtime = runtime
        self.ledger = ledger
        self.observe_only = observe_only
        self.budget_tracker = budget_tracker
        self.action_cost_usd = action_cost_usd

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

        if self.budget_tracker is not None:
            allowed, reason = self.budget_tracker.can_spend(self.action_cost_usd)
            if not allowed:
                result.success = False
                result.blocked_reason = reason
                self._record(result, desired=desired, observed=observed, action=None)
                return result

        if self.observe_only:
            result.actions_taken.append(f"would_{action}")
            self._record(result, desired=desired, observed=observed, action=action)
            return result

        preflight = mutation_preflight_for(self.runtime, action)
        if preflight.status != MutationReadinessStatus.READY:
            result.success = False
            result.blocked_reason = (
                preflight.reason
                or f"Mutation preflight {preflight.status.value} for action={action}"
            )
            self._record(result, desired=desired, observed=observed, action=None)
            return result

        self.runtime.relaunch(action)
        result.actions_taken.append(action)
        if self.budget_tracker is not None:
            self.budget_tracker.record_spend(self.action_cost_usd)

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
