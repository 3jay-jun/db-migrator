from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from db_migrator.config.models import AppConfig, ExistingTablePolicy, TargetEnvironment


@dataclass(frozen=True)
class DryRunGateDecision:
    allowed: bool
    required: bool
    message: str


DESTRUCTIVE_POLICIES = {
    ExistingTablePolicy.SYNC,
    ExistingTablePolicy.TRUNCATE_RELOAD,
    ExistingTablePolicy.OVERWRITE,
}


def evaluate_dry_run_gate(
    config: AppConfig,
    dry_run_report_path: Path | None,
    *,
    destructive_candidate_count: int | None = None,
) -> DryRunGateDecision:
    """Return whether a GUI-triggered write operation can run after dry-run review."""
    has_destructive_policy = (
        destructive_candidate_count > 0
        if destructive_candidate_count is not None
        else config.migration.existing_table_policy in DESTRUCTIVE_POLICIES
    )
    requires_dry_run = (
        has_destructive_policy
        or config.target.environment is TargetEnvironment.PRODUCTION
    )
    if not requires_dry_run:
        return DryRunGateDecision(allowed=True, required=False, message="Dry-run review is not required for this operation.")

    if dry_run_report_path is not None and dry_run_report_path.exists():
        return DryRunGateDecision(allowed=True, required=True, message="Dry-run report exists.")

    configured_path = config.migration.dry_run_report_path
    if configured_path is not None and Path(configured_path).exists():
        return DryRunGateDecision(allowed=True, required=True, message="Configured dry-run report exists.")

    return DryRunGateDecision(
        allowed=False,
        required=True,
        message="Dry-run report is required before running destructive or production-target operations.",
    )
