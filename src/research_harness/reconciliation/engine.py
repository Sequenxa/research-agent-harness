from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from research_harness.adapters.base import RuntimeAdapter
from research_harness.budget import BudgetTracker
from research_harness.contract.models import ProjectContract
from research_harness.ledger import LedgerEventType, LedgerStore
from research_harness.models.enums import MutationReadinessStatus, RuntimeFreshness
from research_harness.models.state import (
    DeploymentDelta,
    DesiredState,
    ObservedState,
    ReconciliationDifference,
    ReconciliationResult,
)
from research_harness.runtime.desired import (
    build_desired_state,
    merge_repository_deployment_fields,
)
from research_harness.runtime.fingerprint import (
    compare_fingerprints,
    select_relaunch_action,
)
from research_harness.runtime.io import write_fingerprint_file
from research_harness.runtime.mutation import remediate_preflight


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
        persist_desired_path: Path | None = None,
    ) -> None:
        self.contract = contract
        self.runtime = runtime
        self.ledger = ledger
        self.observe_only = observe_only
        self.budget_tracker = budget_tracker
        self.action_cost_usd = action_cost_usd
        self.persist_desired_path = persist_desired_path

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

        field_classes = self.runtime.fingerprint_field_classifications()
        result.deployment_delta = DeploymentDelta(
            changed_fields=list(comparison.changed_fields),
            required_action=action,
            field_classes={
                field: field_classes.get(field, "deployment")
                for field in comparison.changed_fields
            },
        )

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
            remediation = remediate_preflight(
                self.runtime, action, observe_only=True
            )
            if remediation.final.status == MutationReadinessStatus.REPAIRABLE:
                for repair_id in remediation.repairs_applied:
                    result.actions_taken.append(f"would_repair:{repair_id}")
            result.actions_taken.append(f"would_{action}")
            self._record(result, desired=desired, observed=observed, action=action)
            return result

        remediation = remediate_preflight(self.runtime, action)
        for repair_id in remediation.repairs_applied:
            result.actions_taken.append(f"repair:{repair_id}")
        if remediation.repairs_applied:
            desired = self._sync_desired_after_repairs(desired, result)
        if remediation.final.status != MutationReadinessStatus.READY:
            result.success = False
            result.blocked_reason = (
                remediation.blocked_reason
                or remediation.final.reason
                or f"Mutation preflight {remediation.final.status.value} for action={action}"
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

    def _sync_desired_after_repairs(
        self,
        desired: DesiredState,
        result: ReconciliationResult,
    ) -> DesiredState:
        synced_fp, synced_fields = merge_repository_deployment_fields(
            self.runtime,
            desired.fingerprint,
        )
        if not synced_fields:
            return desired
        updated = build_desired_state(
            self.contract,
            fingerprint_fields=synced_fp,
            lifecycle=desired.lifecycle,
        )
        for field in synced_fields:
            result.actions_taken.append(f"sync_desired:{field}")
        if self.persist_desired_path is not None:
            write_fingerprint_file(self.persist_desired_path, synced_fp)
        setter = getattr(self.runtime, "set_pending_desired", None)
        if callable(setter):
            setter(synced_fp)
        return updated

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
                "deployment_delta": (
                    result.deployment_delta.model_dump(mode="json")
                    if result.deployment_delta is not None
                    else None
                ),
                "desired_fingerprint": desired.fingerprint,
                "observed_fingerprint": observed.fingerprint,
                "runtime_freshness": (
                    RuntimeFreshness.CURRENT.value
                    if not result.differences or result.success
                    else RuntimeFreshness.STALE.value
                ),
            },
        )
