from __future__ import annotations

import csv
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from html import escape
from pathlib import Path

from db_migrator.adapters.base import DdlGenerator, DdlResult
from db_migrator.adapters.registry import DbmsAdapterRegistry, default_adapter_registry
from db_migrator.config.models import AppConfig, Dbms
from db_migrator.core.indexes import IndexMigrationDecision, plan_index_migration
from db_migrator.reports.labels import display_timestamp, option_label
from db_migrator.reports.metadata import ReportEndpoint
from db_migrator.schema.column_plan import ColumnPlan
from db_migrator.schema.models import SchemaObjectKind, SchemaSnapshot, TableSchema
from db_migrator.schema.schema_pair import SchemaOrigin


@dataclass(frozen=True)
class DryRunWarning:
    message: str
    severity: str
    action: str


@dataclass(frozen=True)
class DryRunTableResult:
    schema: str
    table: str
    source_schema: str
    source_table: str
    source_ddl: str
    target_ddl: str
    ddl: str
    schema_origin: str
    source_only_columns: tuple[str, ...]
    alter_table_candidates: tuple[str, ...]
    warning_count: int
    warnings: tuple[DryRunWarning, ...]


@dataclass(frozen=True)
class DryRunObjectCheck:
    object_type: str
    count: int
    severity: str
    message: str
    action: str


@dataclass(frozen=True)
class DryRunMetadata:
    generated_at: str
    source: ReportEndpoint | None = None
    target: ReportEndpoint | None = None
    migration_mode: str | None = None
    existing_table_policy: str | None = None
    ddl_execution: str = "not_executed"


@dataclass(frozen=True)
class DryRunReport:
    tables: tuple[DryRunTableResult, ...]
    object_checks: tuple[DryRunObjectCheck, ...] = field(default_factory=tuple)
    index_plan: tuple[IndexMigrationDecision, ...] = field(default_factory=tuple)
    metadata: DryRunMetadata = field(default_factory=lambda: _default_metadata())

    @property
    def table_count(self) -> int:
        return len(self.tables)

    @property
    def warning_count(self) -> int:
        return sum(table.warning_count for table in self.tables)


def build_dry_run_report(
    snapshot: SchemaSnapshot,
    *,
    source_snapshot: SchemaSnapshot | None = None,
    source_dbms: Dbms = Dbms.POSTGRESQL,
    target_dbms: Dbms = Dbms.MYSQL,
    target_database: str | None = None,
    metadata: DryRunMetadata | None = None,
    registry: DbmsAdapterRegistry | None = None,
    app_config: AppConfig | None = None,
    column_plans: dict[str, ColumnPlan] | None = None,
    schema_origins: dict[str, SchemaOrigin] | None = None,
) -> DryRunReport:
    adapter_registry = registry or default_adapter_registry()
    source_tables = source_snapshot.tables if source_snapshot is not None else snapshot.tables
    if len(source_tables) != len(snapshot.tables):
        raise ValueError("Source and target schema snapshots must contain the same number of tables for dry-run DDL comparison.")
    source_generator_factory = getattr(adapter_registry, "create_source_ddl_generator", None)
    source_generator = (
        source_generator_factory(source_dbms)
        if source_generator_factory is not None
        else adapter_registry.create_ddl_generator(source_dbms)
    )
    target_generator = adapter_registry.create_ddl_generator(target_dbms, target_database=target_database)
    object_source = source_snapshot or snapshot
    index_plan = plan_index_migration(snapshot, config=app_config or _index_plan_default_config(target_dbms), target_dbms=target_dbms)
    return DryRunReport(
        tables=tuple(
            _build_table_result(
                source_generator,
                target_generator,
                source_table,
                target_table,
                column_plan=(column_plans or {}).get(_table_key(source_table)),
                schema_origin=(schema_origins or {}).get(_table_key(source_table), SchemaOrigin.SOURCE_MAPPED),
            )
            for source_table, target_table in zip(source_tables, snapshot.tables, strict=True)
        ),
        object_checks=_build_object_checks(object_source, index_plan=index_plan),
        index_plan=index_plan,
        metadata=metadata or _default_metadata(),
    )


def write_dry_run_report(report: DryRunReport, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_json(report, output_dir / "summary.json")
    _write_csv(report, output_dir / "tables.csv")
    _write_html(report, output_dir / "summary.html")


def _build_table_result(
    source_generator: DdlGenerator,
    target_generator: DdlGenerator,
    source_table: TableSchema,
    target_table: TableSchema,
    column_plan: ColumnPlan | None,
    schema_origin: SchemaOrigin,
) -> DryRunTableResult:
    source_ddl_result: DdlResult = source_generator.generate_create_table(source_table)
    target_ddl_result: DdlResult = target_generator.generate_create_table(target_table)
    warning_messages = list(target_ddl_result.warnings)
    if source_table.primary_key is None and not any(index.unique for index in source_table.indexes):
        warning_messages.append("high risk: 기본키 또는 unique index가 없어 offset 기준 resume만 가능합니다.")
    source_only_column_plans = column_plan.source_only_columns if column_plan is not None else ()
    source_only_columns = tuple(item.column.name for item in source_only_column_plans)
    alter_table_candidates = (
        (column_plan.rename_column_ddls + column_plan.type_change_ddls + column_plan.add_column_ddls) if column_plan is not None else ()
    ) + tuple(
        item.alter_table_ddl for item in source_only_column_plans if item.alter_table_ddl
    )
    for item in source_only_column_plans:
        if item.action.value == "manual":
            warning_messages.append(f"source-only column ignored: {item.column.name}")
        elif item.action.value == "add_to_target":
            warning_messages.append(f"source-only column selected as ALTER candidate: {item.column.name}")
        else:
            warning_messages.append(f"source-only column ignored: {item.column.name}")
    if column_plan is not None:
        for item in column_plan.unresolved_target_columns:
            warning_messages.append(f"unresolved target column: {item.column.name} - {item.message}")
    warnings = tuple(_build_warning(message) for message in warning_messages)
    return DryRunTableResult(
        schema=target_table.ref.schema,
        table=target_table.ref.name,
        source_schema=source_table.ref.schema,
        source_table=source_table.ref.name,
        source_ddl=source_ddl_result.ddl,
        target_ddl=target_ddl_result.ddl,
        ddl=target_ddl_result.ddl,
        schema_origin=schema_origin.value,
        source_only_columns=source_only_columns,
        alter_table_candidates=alter_table_candidates,
        warning_count=len(warnings),
        warnings=warnings,
    )


def _table_key(table: TableSchema) -> str:
    return f"{table.ref.schema}.{table.ref.name}"


def _default_metadata() -> DryRunMetadata:
    return DryRunMetadata(generated_at=datetime.now(timezone.utc).isoformat())


def _index_plan_default_config(target_dbms: Dbms) -> AppConfig:
    config = AppConfig()
    config.target.dbms = target_dbms
    return config


def _build_object_checks(
    snapshot: SchemaSnapshot,
    *,
    index_plan: tuple[IndexMigrationDecision, ...] = (),
) -> tuple[DryRunObjectCheck, ...]:
    object_counts = {
        "index": _count_manual_review_indexes(index_plan),
        "view": _count_objects(snapshot, SchemaObjectKind.VIEW),
        "function": _count_objects(snapshot, SchemaObjectKind.FUNCTION),
        "procedure": _count_objects(snapshot, SchemaObjectKind.PROCEDURE),
        "trigger": _count_objects(snapshot, SchemaObjectKind.TRIGGER),
    }
    return tuple(_build_object_check(object_type, count) for object_type, count in object_counts.items())


def _count_manual_review_indexes(index_plan: tuple[IndexMigrationDecision, ...]) -> int:
    return sum(1 for decision in index_plan if decision.timing == "manual_review")


def _count_objects(snapshot: SchemaSnapshot, kind: SchemaObjectKind) -> int:
    return sum(1 for schema_object in snapshot.non_table_objects if schema_object.kind is kind)


def _build_object_check(object_type: str, count: int) -> DryRunObjectCheck:
    label = _object_type_label(object_type)
    if count == 0:
        return DryRunObjectCheck(
            object_type=label,
            count=0,
            severity="없음",
            message=f"{label} 객체가 감지되지 않았습니다.",
            action="추가 조치가 필요하지 않습니다.",
        )
    return DryRunObjectCheck(
        object_type=label,
        count=count,
        severity="수동 검토",
        message=f"{label} 객체 {count}건이 원본 스키마에서 감지되었습니다.",
        action=_object_check_action(object_type),
    )


def _object_type_label(object_type: str) -> str:
    labels = {
        "index": "인덱스",
        "view": "뷰",
        "function": "함수",
        "procedure": "프로시저",
        "trigger": "트리거",
    }
    return labels[object_type]


def _object_check_action(object_type: str) -> str:
    actions = {
        "index": "secondary index는 기본 CREATE TABLE DDL에 포함되지 않으므로 대상 DBMS 기준으로 별도 생성 DDL을 검토하세요.",
        "view": "view SQL은 자동 변환하지 않으므로 대상 DBMS 문법과 참조 테이블 매핑을 수동 검토하세요.",
        "function": "function 본문은 자동 변환하지 않으므로 대상 DBMS 함수 문법과 권한을 수동 검토하세요.",
        "procedure": "procedure 본문은 자동 변환하지 않으므로 대상 DBMS 프로시저 문법과 권한을 수동 검토하세요.",
        "trigger": "trigger는 자동 변환하지 않으므로 이벤트 타이밍, 참조 컬럼, 권한을 대상 DBMS 기준으로 수동 검토하세요.",
    }
    return actions[object_type]


def _write_json(report: DryRunReport, output_path: Path) -> None:
    output_path.write_text(
        json.dumps(asdict(report), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _write_csv(report: DryRunReport, output_path: Path) -> None:
    with output_path.open("w", encoding="utf-8", newline="") as csv_file:
        writer = csv.writer(csv_file)
        writer.writerow(["schema", "table", "schema_origin", "source_only_columns", "alter_table_candidates", "warning_count", "warnings", "recommended_actions"])
        for table in report.tables:
            writer.writerow(
                [
                    table.schema,
                    table.table,
                    table.schema_origin,
                    " | ".join(table.source_only_columns),
                    " | ".join(table.alter_table_candidates),
                    table.warning_count,
                    " | ".join(warning.message for warning in table.warnings),
                    " | ".join(warning.action for warning in table.warnings),
                ]
            )


def _write_html(report: DryRunReport, output_path: Path) -> None:
    warning_rows = "\n".join(_warning_row(table) for table in report.tables if table.warnings)
    if not warning_rows:
        warning_rows = """
          <tr>
            <td colspan="5" class="empty-state">검토 필요 항목이 없습니다. 대상 테이블 생성 SQL 실행 검토 단계로 진행할 수 있습니다.</td>
          </tr>
        """
    object_rows = "\n".join(_object_check_row(check) for check in report.object_checks)
    index_plan_rows = "\n".join(_index_plan_row(item) for item in report.index_plan)
    if not index_plan_rows:
        index_plan_rows = """
          <tr>
            <td colspan="7" class="empty-state">자동 생성 후보 또는 수동 검토가 필요한 보조 인덱스가 없습니다.</td>
          </tr>
        """
    table_rows = "\n".join(_table_row(table) for table in report.tables)
    html = f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <title>Jigration 사전 점검 리포트</title>
  <style>
    :root {{
      color-scheme: light;
      --color-canvas: oklch(1.000 0.000 0);
      --color-canvas-200: oklch(0.976 0.000 0);
      --color-overlay: oklch(1.000 0.000 0);
      --color-primary: oklch(0.000 0.000 0);
      --color-primary-soft: oklch(0.976 0.000 0);
      --color-primary-border: oklch(0.269 0.000 0);
      --color-text-strong: oklch(0.269 0.000 0);
      --color-text-normal: oklch(0.417 0.000 0);
      --color-text-muted: oklch(0.478 0.000 0);
      --color-border: oklch(0.827 0.000 0);
      --color-border-hover: oklch(0.715 0.000 0);
      --color-success-soft: oklch(0.974 0.016 167);
      --color-success-text: oklch(0.407 0.090 162);
      --color-warning-soft: oklch(0.979 0.012 51);
      --color-warning-text: oklch(0.503 0.188 33);
      --color-warning-border: oklch(0.836 0.092 46);
      --color-danger-soft: oklch(0.978 0.011 24);
      --color-danger-text: oklch(0.505 0.196 24);
      --color-code-bg: oklch(0.269 0.000 0);
      --space-050: 4px;
      --space-100: 8px;
      --space-150: 12px;
      --space-200: 16px;
      --space-250: 20px;
      --space-300: 24px;
      --space-400: 32px;
      --radius-200: 6px;
      --radius-300: 8px;
      --radius-400: 12px;
      --radius-circle: 9999px;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--color-canvas-200);
      color: var(--color-text-strong);
      font-family: Pretendard, "Segoe UI", Arial, sans-serif;
      line-height: 1.5;
    }}
    main {{
      width: min(1180px, calc(100% - 48px));
      margin: var(--space-400) auto 48px;
    }}
    header {{
      display: grid;
      gap: var(--space-200);
      margin-bottom: var(--space-300);
    }}
    h1 {{
      margin: 0;
      font-size: 32px;
      line-height: 40px;
      font-weight: 800;
      letter-spacing: 0;
    }}
    h2 {{
      margin: 0 0 var(--space-200);
      font-size: 18px;
      line-height: 26px;
      font-weight: 700;
      letter-spacing: 0;
    }}
    p {{
      margin: 0;
      color: var(--color-text-muted);
      font-size: 14px;
      line-height: 20px;
    }}
    .eyebrow {{
      margin-bottom: var(--space-050);
      color: var(--color-text-muted);
      font-size: 12px;
      font-weight: 600;
      line-height: 18px;
      text-transform: uppercase;
    }}
    .summary-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: var(--space-150);
    }}
    .metric {{
      background: var(--color-canvas);
      border: 1px solid var(--color-border);
      border-radius: var(--radius-400);
      padding: var(--space-200);
    }}
    .metric span {{
      display: block;
      color: var(--color-text-muted);
      font-size: 13px;
      line-height: 20px;
      font-weight: 500;
      margin-bottom: 6px;
    }}
    .metric strong {{
      display: block;
      color: var(--color-primary);
      font-size: 32px;
      line-height: 40px;
      font-weight: 800;
    }}
    .context-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
      gap: var(--space-150);
    }}
    .context-block {{
      border: 1px solid var(--color-border);
      border-radius: var(--radius-300);
      overflow: hidden;
    }}
    .context-block h3 {{
      margin: 0;
      padding: var(--space-150) var(--space-200);
      background: var(--color-canvas-200);
      color: var(--color-text-strong);
      font-size: 14px;
      line-height: 20px;
      font-weight: 700;
    }}
    .context-list {{
      display: grid;
      grid-template-columns: 120px 1fr;
      margin: 0;
    }}
    .context-list dt,
    .context-list dd {{
      margin: 0;
      padding: var(--space-100) var(--space-150);
      border-top: 1px solid var(--color-border);
      font-size: 13px;
      line-height: 19px;
      word-break: break-word;
    }}
    .context-list dt {{
      color: var(--color-text-muted);
      font-weight: 700;
    }}
    .context-list dd {{
      color: var(--color-text-normal);
    }}
    section {{
      background: var(--color-canvas);
      border: 1px solid var(--color-border);
      border-radius: var(--radius-400);
      padding: var(--space-300);
      margin-top: var(--space-200);
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      table-layout: fixed;
    }}
    th, td {{
      padding: var(--space-150) var(--space-200);
      border-bottom: 1px solid var(--color-border);
      vertical-align: top;
      text-align: left;
      word-break: break-word;
      color: var(--color-text-normal);
      font-size: 14px;
      line-height: 20px;
    }}
    th {{
      background: var(--color-canvas-200);
      color: var(--color-text-muted);
      font-size: 13px;
      font-weight: 700;
      text-transform: none;
    }}
    tr:last-child td {{
      border-bottom: 0;
    }}
    tbody tr:hover td {{
      background: var(--color-canvas-200);
    }}
    .badge {{
      display: inline-flex;
      align-items: center;
      min-height: 22px;
      padding: 0 var(--space-100);
      border-radius: var(--radius-circle);
      font-size: 12px;
      line-height: 18px;
      font-weight: 600;
      background: var(--color-primary);
      color: var(--color-canvas);
    }}
    .badge.warning {{
      background: var(--color-warning-soft);
      color: var(--color-warning-text);
      border: 1px solid var(--color-warning-border);
    }}
    .badge.ok {{
      background: var(--color-success-soft);
      color: var(--color-success-text);
    }}
    .message {{
      font-weight: 600;
      color: var(--color-text-strong);
    }}
    .action {{
      color: var(--color-text-muted);
    }}
    .empty-state {{
      text-align: center;
      color: var(--color-success-text);
      padding: var(--space-300);
    }}
    details {{
      margin: 0;
    }}
    summary {{
      cursor: pointer;
      color: var(--color-primary);
      font-weight: 600;
      outline: none;
    }}
    summary:hover {{
      color: var(--color-text-normal);
    }}
    summary:focus-visible {{
      border-radius: var(--radius-200);
      outline: 2px solid var(--color-primary);
      outline-offset: 2px;
    }}
    details.ddl-details {{
      width: 100%;
    }}
    .ddl-summary {{
      display: grid;
      grid-template-columns: 18% 22% 14% 46%;
      align-items: start;
      min-height: 48px;
      cursor: pointer;
      list-style: none;
    }}
    .ddl-summary::-webkit-details-marker {{
      display: none;
    }}
    .ddl-summary > span {{
      padding: var(--space-150) var(--space-200);
      color: var(--color-text-normal);
      font-size: 14px;
      line-height: 20px;
      word-break: break-word;
    }}
    .ddl-summary > .summary-table {{
      color: var(--color-primary);
      font-weight: 700;
    }}
    .ddl-summary:hover > span {{
      background: var(--color-canvas-200);
    }}
    .ddl-summary:hover > .summary-table {{
      color: var(--color-text-normal);
    }}
    .ddl-summary:focus-visible {{
      border-radius: var(--radius-200);
      outline: 2px solid var(--color-primary);
      outline-offset: 2px;
    }}
    pre {{
      overflow-x: auto;
      padding: var(--space-150);
      border-radius: var(--radius-300);
      background: var(--color-code-bg);
      color: #e5e7eb;
      font-size: 12px;
      line-height: 18px;
    }}
    .ddl-compare {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
      gap: var(--space-150);
      margin: 0 var(--space-200) var(--space-200);
    }}
    .ddl-panel {{
      min-width: 0;
    }}
    .ddl-panel h3 {{
      margin: 0 0 var(--space-100);
      color: var(--color-text-muted);
      font-size: 13px;
      line-height: 20px;
      font-weight: 700;
    }}
    .sql-inline {{
      font-family: Consolas, "Courier New", monospace;
      font-size: 12px;
      line-height: 18px;
    }}
  </style>
</head>
<body>
  <main>
    <header>
      <div>
        <div class="eyebrow">사전 점검 리포트</div>
        <h1>Jigration 사전 점검 리포트</h1>
        <p>대상 테이블 생성 SQL을 실행하기 전에 스키마 변환 위험과 조치사항을 검토하는 리포트입니다.</p>
      </div>
      <div class="summary-grid">
        <div class="metric"><span>총 테이블 수</span><strong>{report.table_count}</strong></div>
        <div class="metric"><span>검토 필요 항목 수</span><strong>{report.warning_count}</strong></div>
        <div class="metric"><span>수동 검토 객체 수</span><strong>{_manual_review_object_count(report)}</strong></div>
      </div>
    </header>

    <section>
      <h2>이관 정보</h2>
      <div class="context-grid">
        {_endpoint_block("원본 DB", report.metadata.source)}
        {_endpoint_block("대상 DB", report.metadata.target)}
        {_dry_run_context_block(report)}
      </div>
    </section>

    <section>
      <h2>검토 필요 항목 및 권장 조치</h2>
      <table>
        <thead>
          <tr>
            <th style="width: 14%;">스키마</th>
            <th style="width: 18%;">테이블명</th>
            <th style="width: 12%;">위험도</th>
            <th style="width: 28%;">검토 필요 항목</th>
            <th style="width: 28%;">권장 조치</th>
          </tr>
        </thead>
        <tbody>
{warning_rows}
        </tbody>
      </table>
    </section>

    <section>
      <h2>수동 검토 객체</h2>
      <table>
        <thead>
          <tr>
            <th style="width: 18%;">객체</th>
            <th style="width: 12%;">건수</th>
            <th style="width: 14%;">상태</th>
            <th style="width: 28%;">검증 결과</th>
            <th style="width: 28%;">권장 조치</th>
          </tr>
        </thead>
        <tbody>
{object_rows}
        </tbody>
      </table>
    </section>

    <section>
      <h2>인덱스 생성 계획</h2>
      <table>
        <thead>
          <tr>
            <th style="width: 12%;">스키마</th>
            <th style="width: 16%;">테이블명</th>
            <th style="width: 18%;">인덱스명</th>
            <th style="width: 12%;">상태</th>
            <th style="width: 14%;">컬럼</th>
            <th style="width: 14%;">생성 SQL</th>
            <th style="width: 14%;">권장 조치</th>
          </tr>
        </thead>
        <tbody>
{index_plan_rows}
        </tbody>
      </table>
    </section>

    <section>
      <h2>테이블 생성 SQL 비교</h2>
      <table>
        <thead>
          <tr>
            <th style="width: 18%;">스키마</th>
            <th style="width: 22%;">테이블명</th>
            <th style="width: 14%;">검토 필요 항목</th>
            <th>SQL 비교</th>
          </tr>
        </thead>
        <tbody>
{table_rows}
        </tbody>
      </table>
    </section>
  </main>
</body>
</html>
"""
    output_path.write_text(html, encoding="utf-8")


def _build_warning(message: str) -> DryRunWarning:
    normalized_message = message.lower()
    return DryRunWarning(
        message=_warning_message_label(message),
        severity=_warning_severity(normalized_message),
        action=_warning_action(normalized_message),
    )


def _warning_severity(normalized_message: str) -> str:
    if "manual review" in normalized_message or "unknown" in normalized_message or "array" in normalized_message:
        return "수동 검토"
    return "검토 필요"


def _warning_message_label(message: str) -> str:
    normalized_message = message.lower()
    if "timestamp with time zone" in normalized_message:
        return "PostgreSQL timestamp with time zone은 MySQL datetime으로 변환되므로 timezone 검토가 필요합니다."
    if "timestamptz" in normalized_message:
        return "PostgreSQL timestamptz는 MySQL datetime으로 변환되므로 timezone 검토가 필요합니다."
    if "time with time zone" in normalized_message:
        return "PostgreSQL time with time zone은 MySQL time으로 변환되므로 timezone 검토가 필요합니다."
    if "jsonb" in normalized_message:
        return "PostgreSQL jsonb는 MySQL JSON과 의미/인덱스 동작이 달라 검토가 필요합니다."
    if "uuid" in normalized_message:
        return "PostgreSQL UUID는 MySQL char(36)으로 변환됩니다."
    if "generated column" in normalized_message:
        return "Generated column 표현식은 MySQL/MariaDB 기준으로 재검토가 필요합니다."
    if "array" in normalized_message:
        return "PostgreSQL array 타입은 명시적인 target 모델 검토가 필요합니다."
    if "offset resume only" in normalized_message:
        return "기본키 또는 unique index가 없어 offset 기준 resume만 가능합니다."
    if "source-only column" in normalized_message:
        return message
    if "unresolved target column" in normalized_message:
        return message
    if "requires manual review" in normalized_message:
        return "자동 변환이 어려운 타입이 있어 수동 검토가 필요합니다."
    return message


def _warning_action(normalized_message: str) -> str:
    if "timestamp with time zone" in normalized_message or "timestamptz" in normalized_message:
        return (
            "원본 timezone 정책을 확인하세요. 절대 시각 보존이 필요하면 이관 전/중에 기준 timezone으로 정규화하고 적재 후 샘플 검증을 수행하세요."
        )
    if "jsonb" in normalized_message:
        return (
            "애플리케이션의 JSON query/index 사용 방식을 확인하세요. MySQL JSON은 유효한 JSON을 저장하지만 PostgreSQL jsonb operator/index와 동일하지 않습니다."
        )
    if "uuid" in normalized_message:
        return "UUID 저장에 char(36)이 적합한지 확인하고 primary key/index 크기와 성능 영향을 검토하세요."
    if "generated column" in normalized_message:
        return "테이블 생성 SQL 실행 전에 generated column 표현식을 MySQL/MariaDB 기준으로 재작성하고 테스트하세요."
    if "array" in normalized_message:
        return "테이블 생성 SQL 실행 전에 JSON text 또는 child table 등 명시적인 대상 모델을 결정하세요."
    if "offset resume only" in normalized_message:
        return "resume/retry 안정성을 높이려면 기본키 또는 unique index 추가 가능 여부를 검토하세요."
    if "source-only column selected as alter candidate" in normalized_message:
        return "자동 ALTER는 실행하지 않습니다. 생성 후보 DDL을 검토한 뒤 별도 적용하세요."
    if "source-only column requires manual" in normalized_message:
        return "target schema에 없는 source 컬럼입니다. 별도 수동 이관 또는 스키마 변경 여부를 결정하세요."
    if "source-only column ignored" in normalized_message:
        return "무시로 선택된 컬럼입니다. 이관하려면 컬럼 매핑에서 대상 컬럼을 지정하세요."
    if "unresolved target column" in normalized_message:
        return "target 필수 컬럼을 채울 source/default/null mapping을 config에 추가하세요."
    if "requires manual review" in normalized_message:
        return "테이블 생성 SQL 실행 전에 명시적인 타입 매핑 또는 별도 이관 규칙을 정의하세요."
    return "테이블 생성 SQL 실행 전에 생성된 SQL과 애플리케이션 동작 영향을 검토하세요."


def _manual_review_object_count(report: DryRunReport) -> int:
    return sum(check.count for check in report.object_checks)


def _endpoint_block(title: str, endpoint: ReportEndpoint | None) -> str:
    if endpoint is None:
        rows = _context_rows((("DBMS", "-"), ("호스트", "-"), ("데이터베이스", "-"), ("스키마", "-")))
    else:
        rows = _context_rows(
            (
                ("DBMS", endpoint.dbms.upper()),
                ("호스트", f"{endpoint.host}:{endpoint.port}"),
                ("데이터베이스", endpoint.database),
                ("스키마", endpoint.schema or "-"),
            )
        )
    return f"""
        <div class="context-block">
          <h3>{escape(title)}</h3>
          <dl class="context-list">
{rows}
          </dl>
        </div>
    """


def _dry_run_context_block(report: DryRunReport) -> str:
    rows = _context_rows(
        (
            ("실행 시각", display_timestamp(report.metadata.generated_at)),
            ("이관 모드", option_label(report.metadata.migration_mode)),
            ("기존 테이블 처리", option_label(report.metadata.existing_table_policy)),
            ("테이블 생성 SQL 실행", option_label(report.metadata.ddl_execution)),
        )
    )
    return f"""
        <div class="context-block">
          <h3>사전 점검 기준</h3>
          <dl class="context-list">
{rows}
          </dl>
        </div>
    """


def _context_rows(rows: tuple[tuple[str, str], ...]) -> str:
    return "\n".join(f"            <dt>{escape(label)}</dt><dd>{escape(value)}</dd>" for label, value in rows)


def _warning_row(table: DryRunTableResult) -> str:
    return "\n".join(
        f"""
          <tr>
            <td>{escape(table.schema)}</td>
            <td>{escape(table.table)}</td>
            <td><span class="badge warning">{escape(warning.severity)}</span></td>
            <td class="message">{escape(warning.message)}</td>
            <td class="action">{escape(warning.action)}</td>
          </tr>
        """
        for warning in table.warnings
    )


def _object_check_row(check: DryRunObjectCheck) -> str:
    badge_class = "ok" if check.count == 0 else "warning"
    return f"""
          <tr>
            <td>{escape(check.object_type)}</td>
            <td>{check.count}</td>
            <td><span class="badge {badge_class}">{escape(check.severity)}</span></td>
            <td class="message">{escape(check.message)}</td>
            <td class="action">{escape(check.action)}</td>
          </tr>
    """


def _index_plan_row(item: IndexMigrationDecision) -> str:
    badge_class = "ok" if item.timing in {"pre_data", "post_data"} else "warning"
    ddl = item.ddl or item.reason or "-"
    return f"""
          <tr>
            <td>{escape(item.schema)}</td>
            <td>{escape(item.table)}</td>
            <td class="message">{escape(item.index)}</td>
            <td><span class="badge {badge_class}">{escape(_index_timing_label(item.timing))}</span></td>
            <td>{escape(', '.join(item.columns))}</td>
            <td class="sql-inline">{escape(ddl)}</td>
            <td class="action">{escape(item.reason)}</td>
          </tr>
    """


def _index_timing_label(timing: str) -> str:
    return {
        "pre_data": "선생성",
        "post_data": "후생성",
        "manual_review": "수동 검토",
        "skip": "건너뜀",
    }.get(timing, timing)


def _table_row(table: DryRunTableResult) -> str:
    warning_badge = (
        f'<span class="badge warning">{table.warning_count}건</span>'
        if table.warning_count
        else '<span class="badge ok">없음</span>'
    )
    return f"""
          <tr>
            <td colspan="4">
              <details class="ddl-details">
                <summary class="ddl-summary">
                  <span>{escape(table.schema)}</span>
                  <span class="summary-table">{escape(table.table)}</span>
                  <span>{warning_badge}</span>
                  <span class="action">스키마 기준: {escape(table.schema_origin)} / 원본 / 대상 테이블 생성 SQL 비교</span>
                </summary>
                <div class="ddl-compare">
                  <div class="ddl-panel">
                    <h3>원본 CREATE TABLE ({escape(table.source_schema)}.{escape(table.source_table)})</h3>
                    <pre>{escape(table.source_ddl)}</pre>
                  </div>
                  <div class="ddl-panel">
                    <h3>대상 CREATE TABLE ({escape(table.schema)}.{escape(table.table)})</h3>
                    <pre>{escape(table.target_ddl)}</pre>
                  </div>
                </div>
                {_alter_candidates_block(table)}
              </details>
            </td>
          </tr>
    """


def _alter_candidates_block(table: DryRunTableResult) -> str:
    if not table.alter_table_candidates:
        return ""
    candidates = "\n".join(f"<pre>{escape(candidate)}</pre>" for candidate in table.alter_table_candidates)
    return f"""
                <div class="ddl-compare">
                  <div class="ddl-panel">
                    <h3>Source-only 컬럼 ALTER 후보</h3>
                    {candidates}
                  </div>
                </div>
    """
