from __future__ import annotations

import csv
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from html import escape
from pathlib import Path

from db_migrator.adapters.base import DdlGenerator, DdlResult
from db_migrator.adapters.registry import DbmsAdapterRegistry, default_adapter_registry
from db_migrator.config.models import Dbms
from db_migrator.reports.metadata import ReportEndpoint
from db_migrator.schema.models import SchemaSnapshot, TableSchema


@dataclass(frozen=True)
class DryRunWarning:
    message: str
    severity: str
    action: str


@dataclass(frozen=True)
class DryRunTableResult:
    schema: str
    table: str
    ddl: str
    warning_count: int
    warnings: tuple[DryRunWarning, ...]


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
    target_dbms: Dbms = Dbms.MYSQL,
    target_database: str | None = None,
    metadata: DryRunMetadata | None = None,
    registry: DbmsAdapterRegistry | None = None,
) -> DryRunReport:
    adapter_registry = registry or default_adapter_registry()
    generator = adapter_registry.create_ddl_generator(target_dbms, target_database=target_database)
    return DryRunReport(
        tables=tuple(_build_table_result(generator, table) for table in snapshot.tables),
        metadata=metadata or _default_metadata(),
    )


def write_dry_run_report(report: DryRunReport, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_json(report, output_dir / "summary.json")
    _write_csv(report, output_dir / "tables.csv")
    _write_html(report, output_dir / "summary.html")


def _build_table_result(generator: DdlGenerator, table: TableSchema) -> DryRunTableResult:
    ddl_result: DdlResult = generator.generate_create_table(table)
    warning_messages = list(ddl_result.warnings)
    if table.primary_key is None and not any(index.unique for index in table.indexes):
        warning_messages.append("high risk: 기본키 또는 unique index가 없어 offset 기준 resume만 가능합니다.")
    warnings = tuple(_build_warning(message) for message in warning_messages)
    return DryRunTableResult(
        schema=table.ref.schema,
        table=table.ref.name,
        ddl=ddl_result.ddl,
        warning_count=len(warnings),
        warnings=warnings,
    )


def _default_metadata() -> DryRunMetadata:
    return DryRunMetadata(generated_at=datetime.now(timezone.utc).isoformat())


def _write_json(report: DryRunReport, output_path: Path) -> None:
    output_path.write_text(
        json.dumps(asdict(report), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _write_csv(report: DryRunReport, output_path: Path) -> None:
    with output_path.open("w", encoding="utf-8", newline="") as csv_file:
        writer = csv.writer(csv_file)
        writer.writerow(["schema", "table", "warning_count", "warnings", "recommended_actions"])
        for table in report.tables:
            writer.writerow(
                [
                    table.schema,
                    table.table,
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
            <td colspan="5" class="empty-state">경고가 없습니다. DDL 실행 검토 단계로 진행할 수 있습니다.</td>
          </tr>
        """
    table_rows = "\n".join(_table_row(table) for table in report.tables)
    html = f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <title>Jigration Dry-run 리포트</title>
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
    pre {{
      overflow-x: auto;
      padding: var(--space-150);
      border-radius: var(--radius-300);
      background: var(--color-code-bg);
      color: #e5e7eb;
      font-size: 12px;
      line-height: 18px;
    }}
  </style>
</head>
<body>
  <main>
    <header>
      <div>
        <div class="eyebrow">Dry-run 리포트</div>
        <h1>Jigration Dry-run 리포트</h1>
        <p>Target DDL을 실행하기 전에 스키마 변환 위험과 조치사항을 검토하는 리포트입니다.</p>
      </div>
      <div class="summary-grid">
        <div class="metric"><span>총 테이블 수</span><strong>{report.table_count}</strong></div>
        <div class="metric"><span>총 경고 수</span><strong>{report.warning_count}</strong></div>
        <div class="metric"><span>다음 단계</span><strong>{_next_step_label(report)}</strong></div>
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
      <h2>경고 및 권장 조치</h2>
      <table>
        <thead>
          <tr>
            <th style="width: 14%;">스키마</th>
            <th style="width: 18%;">테이블명</th>
            <th style="width: 12%;">심각도</th>
            <th style="width: 28%;">경고</th>
            <th style="width: 28%;">권장 조치</th>
          </tr>
        </thead>
        <tbody>
{warning_rows}
        </tbody>
      </table>
    </section>

    <section>
      <h2>테이블 DDL 미리보기</h2>
      <table>
        <thead>
          <tr>
            <th style="width: 18%;">스키마</th>
            <th style="width: 22%;">테이블명</th>
            <th style="width: 14%;">경고</th>
            <th>DDL</th>
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
    if "requires manual review" in normalized_message:
        return "자동 변환이 어려운 타입이 있어 수동 검토가 필요합니다."
    return message


def _warning_action(normalized_message: str) -> str:
    if "timestamp with time zone" in normalized_message or "timestamptz" in normalized_message:
        return (
            "source timezone 정책을 확인하세요. 절대 시각 보존이 필요하면 이관 전/중에 기준 timezone으로 정규화하고 적재 후 샘플 검증을 수행하세요."
        )
    if "jsonb" in normalized_message:
        return (
            "애플리케이션의 JSON query/index 사용 방식을 확인하세요. MySQL JSON은 유효한 JSON을 저장하지만 PostgreSQL jsonb operator/index와 동일하지 않습니다."
        )
    if "uuid" in normalized_message:
        return "UUID 저장에 char(36)이 적합한지 확인하고 primary key/index 크기와 성능 영향을 검토하세요."
    if "generated column" in normalized_message:
        return "apply-ddl 실행 전에 generated column 표현식을 MySQL/MariaDB 기준으로 재작성하고 테스트하세요."
    if "array" in normalized_message:
        return "apply-ddl 실행 전에 JSON text 또는 child table 등 명시적인 target 모델을 결정하세요."
    if "offset resume only" in normalized_message:
        return "resume/retry 안정성을 높이려면 기본키 또는 unique index 추가 가능 여부를 검토하세요."
    if "requires manual review" in normalized_message:
        return "apply-ddl 실행 전에 명시적인 타입 매핑 또는 custom migration rule을 정의하세요."
    return "apply-ddl 실행 전에 생성된 DDL과 애플리케이션 동작 영향을 검토하세요."


def _next_step_label(report: DryRunReport) -> str:
    if report.warning_count:
        return "검토 필요"
    return "DDL 실행 검토"


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
            ("실행 시각", report.metadata.generated_at),
            ("이관 모드", _optional_label(report.metadata.migration_mode)),
            ("기존 테이블 정책", _optional_label(report.metadata.existing_table_policy)),
            ("DDL 실행 여부", _ddl_execution_label(report.metadata.ddl_execution)),
            ("결과 요약", f"총 테이블 {report.table_count}, 경고 {report.warning_count}"),
        )
    )
    return f"""
        <div class="context-block">
          <h3>Dry-run 기준</h3>
          <dl class="context-list">
{rows}
          </dl>
        </div>
    """


def _context_rows(rows: tuple[tuple[str, str], ...]) -> str:
    return "\n".join(f"            <dt>{escape(label)}</dt><dd>{escape(value)}</dd>" for label, value in rows)


def _optional_label(value: str | None) -> str:
    if not value:
        return "-"
    return value


def _ddl_execution_label(value: str) -> str:
    if value == "not_executed":
        return "실행 안 함"
    return value


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


def _table_row(table: DryRunTableResult) -> str:
    warning_badge = (
        f'<span class="badge warning">{table.warning_count}건</span>'
        if table.warning_count
        else '<span class="badge ok">없음</span>'
    )
    return f"""
          <tr>
            <td>{escape(table.schema)}</td>
            <td>{escape(table.table)}</td>
            <td>{warning_badge}</td>
            <td>
              <details>
                <summary>CREATE TABLE 보기</summary>
                <pre>{escape(table.ddl)}</pre>
              </details>
            </td>
          </tr>
    """
