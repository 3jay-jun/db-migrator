from db_migrator.config.models import ExistingTablePolicy, SafetyConfig, TargetConfig, TargetEnvironment
from db_migrator.core.safety_guard import (
    SafetyDecisionStatus,
    SafetyGuardInput,
    SafetyRiskCode,
    TargetSafetyGuard,
)


def test_safety_guard_allows_non_destructive_skip_on_production() -> None:
    decision = TargetSafetyGuard().evaluate(
        SafetyGuardInput(
            target=TargetConfig(environment=TargetEnvironment.PRODUCTION),
            safety=SafetyConfig(),
            existing_table_policy=ExistingTablePolicy.SKIP,
            table_count=1,
            estimated_rows=100,
            dry_run_report_exists=False,
        )
    )

    assert decision.status is SafetyDecisionStatus.ALLOWED


def test_safety_guard_blocks_production_destructive_policy() -> None:
    decision = TargetSafetyGuard().evaluate(
        SafetyGuardInput(
            target=TargetConfig(environment=TargetEnvironment.PRODUCTION),
            safety=SafetyConfig(),
            existing_table_policy=ExistingTablePolicy.TRUNCATE_RELOAD,
            table_count=1,
            estimated_rows=100,
            dry_run_report_exists=True,
        )
    )

    assert decision.status is SafetyDecisionStatus.BLOCKED
    assert SafetyRiskCode.PRODUCTION_DESTRUCTIVE_BLOCKED.value in decision.blocking_reasons


def test_safety_guard_requires_dry_run_before_production_destructive_policy() -> None:
    decision = TargetSafetyGuard().evaluate(
        SafetyGuardInput(
            target=TargetConfig(environment=TargetEnvironment.PRODUCTION),
            safety=SafetyConfig(allow_destructive_on_production=True),
            existing_table_policy=ExistingTablePolicy.OVERWRITE,
            table_count=1,
            estimated_rows=100,
            dry_run_report_exists=False,
        )
    )

    assert decision.status is SafetyDecisionStatus.BLOCKED
    assert SafetyRiskCode.DRY_RUN_REQUIRED.value in decision.blocking_reasons


def test_safety_guard_allows_overwrite_policy_when_no_destructive_candidates() -> None:
    decision = TargetSafetyGuard().evaluate(
        SafetyGuardInput(
            target=TargetConfig(environment=TargetEnvironment.PRODUCTION),
            safety=SafetyConfig(),
            existing_table_policy=ExistingTablePolicy.OVERWRITE,
            table_count=1,
            estimated_rows=100,
            dry_run_report_exists=False,
            destructive_table_count=0,
        )
    )

    assert decision.status is SafetyDecisionStatus.ALLOWED


def test_safety_guard_treats_sync_as_destructive_policy() -> None:
    decision = TargetSafetyGuard().evaluate(
        SafetyGuardInput(
            target=TargetConfig(environment=TargetEnvironment.PRODUCTION),
            safety=SafetyConfig(),
            existing_table_policy=ExistingTablePolicy.SYNC,
            table_count=1,
            estimated_rows=100,
            dry_run_report_exists=True,
        )
    )

    assert decision.status is SafetyDecisionStatus.BLOCKED
    assert SafetyRiskCode.PRODUCTION_DESTRUCTIVE_BLOCKED.value in decision.blocking_reasons


def test_safety_guard_warns_on_production_keyword_even_when_env_is_dev() -> None:
    decision = TargetSafetyGuard().evaluate(
        SafetyGuardInput(
            target=TargetConfig(host="prod-db.internal", environment=TargetEnvironment.DEV),
            safety=SafetyConfig(),
            existing_table_policy=ExistingTablePolicy.SKIP,
            table_count=1,
            estimated_rows=100,
            dry_run_report_exists=False,
        )
    )

    assert decision.status is SafetyDecisionStatus.ALLOWED
    assert SafetyRiskCode.PRODUCTION_KEYWORD_DETECTED.value in decision.warnings
