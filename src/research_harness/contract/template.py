from __future__ import annotations

from research_harness.contract.models import (
    AllowListRule,
    AuthorityConfig,
    BudgetConfig,
    CompletionConfig,
    Duration,
    EscalationConfig,
    FingerprintConfig,
    Invariant,
    PhaseConfig,
    ProgressConfig,
    ProgressWatermark,
    ProjectContract,
    ProjectInfo,
    RecoveryConfig,
    ScopedPathRule,
    StableAfterConfig,
    ValidityConfig,
    VerificationConfig,
)


def default_contract(*, project_id: str, objective: str) -> ProjectContract:
    """Return a starter v1.1 contract suitable for ``research-harness init``."""
    return ProjectContract(
        contract_version=1,
        project=ProjectInfo(id=project_id, objective=objective),
        invariants=[
            Invariant(
                id="example-invariant",
                statement="Workers must not read sealed evaluator labels.",
                check="fs_acl.sealed_paths_unreadable_by_worker",
                on_violation="halt",
            )
        ],
        fingerprint=FingerprintConfig(
            fields=[
                "git_sha",
                "lock_hash",
                "model",
                "provider",
                "prompt_version",
                "dataset_version",
                "evaluator_version",
                "config_hash",
            ],
            on_change={
                "prompt_version": "worker_restart",
                "config_hash": "worker_restart",
                "lock_hash": "rebuild",
                "model": "full_relaunch",
                "git_sha": "full_relaunch",
            },
            default="full_relaunch",
        ),
        progress=ProgressConfig(
            watermarks=[
                ProgressWatermark(
                    name="completed_units",
                    source="ledger",
                    stall_after=Duration.parse("20m"),
                ),
                ProgressWatermark(
                    name="worker_heartbeat",
                    source="adapter",
                    stall_after=Duration.parse("90s"),
                ),
            ],
            phases={
                "dataset_load": PhaseConfig(stall_after=Duration.parse("45m")),
                "warmup": PhaseConfig(stall_after=Duration.parse("15m")),
            },
            slow_operation_grace=Duration.parse("60m"),
            stall_requires="any",
        ),
        validity=ValidityConfig(
            expected_units=1000,
            max_null_rate=0.02,
            max_error_rate=0.05,
            require_fingerprint_match=True,
            on_invalid="quarantine",
        ),
        authority=AuthorityConfig(
            code_changes=ScopedPathRule(
                allow=["src/workers/**", "configs/**"],
                deny=["src/evaluator/**", "data/**"],
            ),
            dependency_changes="patch_only",
            model_swaps=AllowListRule(allow=["gpt-4o-mini", "claude-haiku-4-5"]),
            provider_swaps=True,
            runtime_restarts=True,
            architecture_changes=False,
        ),
        budget=BudgetConfig(
            total_usd=100,
            per_hour_usd=15,
            per_incident_usd=5,
            per_incident_wallclock=Duration.parse("30m"),
            warn_at=0.7,
        ),
        recovery=RecoveryConfig(
            max_identical_attempts=2,
            max_attempts_per_incident=6,
            detect_oscillation=True,
            oscillation_window=4,
            backoff="exponential",
            min_backoff=Duration.parse("30s"),
            novel_strategy_requires="evidence_delta",
        ),
        verification=VerificationConfig(
            smoke_test="required",
            stable_after=StableAfterConfig(
                units=5,
                min_duration=Duration.parse("10m"),
                no_recurrence_within=Duration.parse("30m"),
            ),
        ),
        completion=CompletionConfig(
            condition="units_completed >= 1000 and validity.passed",
            on_complete=["snapshot_ledger", "stop_workers"],
        ),
        escalation=EscalationConfig(
            channel="file",
            blocking_timeout=Duration.parse("24h"),
            on_timeout="stop",
        ),
    )
