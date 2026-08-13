from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from db_migrator.config.models import ExistingTablePolicy, SafetyConfig, TargetConfig, TargetEnvironment


class SafetyDecisionStatus(StrEnum):
    ALLOWED = "allowed"
    BLOCKED = "blocked"


class SafetyRiskCode(StrEnum):
    PRODUCTION_DESTRUCTIVE_BLOCKED = "production_destructive_blocked"
    DRY_RUN_REQUIRED = "dry_run_required"
    PRODUCTION_KEYWORD_DETECTED = "production_keyword_detected"


@dataclass(frozen=True)
class SafetyGuardInput:
    target: TargetConfig
    safety: SafetyConfig
    existing_table_policy: ExistingTablePolicy
    table_count: int
    estimated_rows: int | None
    dry_run_report_exists: bool
    destructive_table_count: int | None = None


@dataclass(frozen=True)
class SafetyDecision:
    status: SafetyDecisionStatus
    warnings: tuple[str, ...] = field(default_factory=tuple)
    blocking_reasons: tuple[str, ...] = field(default_factory=tuple)

    @property
    def allowed(self) -> bool:
        return self.status is SafetyDecisionStatus.ALLOWED


class TargetSafetyGuard:
    def evaluate(self, guard_input: SafetyGuardInput) -> SafetyDecision:
        warnings: list[str] = []
        blocking_reasons: list[str] = []

        if _has_production_keyword(guard_input.target):
            warnings.append(SafetyRiskCode.PRODUCTION_KEYWORD_DETECTED.value)

        if not _has_destructive_work(guard_input):
            return SafetyDecision(status=SafetyDecisionStatus.ALLOWED, warnings=tuple(warnings))

        is_production = guard_input.target.environment is TargetEnvironment.PRODUCTION
        if not guard_input.safety.is_production_protection:
            return SafetyDecision(status=SafetyDecisionStatus.ALLOWED, warnings=tuple(warnings))

        if is_production and not guard_input.safety.allow_destructive_on_production:
            blocking_reasons.append(SafetyRiskCode.PRODUCTION_DESTRUCTIVE_BLOCKED.value)

        if is_production and guard_input.safety.require_dry_run_before_destructive:
            if not guard_input.dry_run_report_exists:
                blocking_reasons.append(SafetyRiskCode.DRY_RUN_REQUIRED.value)

        status = SafetyDecisionStatus.BLOCKED if blocking_reasons else SafetyDecisionStatus.ALLOWED
        return SafetyDecision(
            status=status,
            warnings=tuple(warnings),
            blocking_reasons=tuple(blocking_reasons),
        )


def _is_destructive_policy(existing_table_policy: ExistingTablePolicy) -> bool:
    return existing_table_policy in {
        ExistingTablePolicy.SYNC,
        ExistingTablePolicy.TRUNCATE_RELOAD,
        ExistingTablePolicy.OVERWRITE,
    }


def _has_destructive_work(guard_input: SafetyGuardInput) -> bool:
    if guard_input.destructive_table_count is not None:
        return guard_input.destructive_table_count > 0
    return _is_destructive_policy(guard_input.existing_table_policy)


def _has_production_keyword(target: TargetConfig) -> bool:
    haystack = " ".join([target.host, target.database, target.user]).lower()
    return any(keyword in haystack for keyword in ("prod", "live", "real", "operation"))
