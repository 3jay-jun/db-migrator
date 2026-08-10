from __future__ import annotations

import csv
import json
from dataclasses import asdict, dataclass
from html import escape
from pathlib import Path

from db_migrator.adapters.base import DdlGenerator, DdlResult
from db_migrator.adapters.registry import DbmsAdapterRegistry, default_adapter_registry
from db_migrator.config.models import Dbms
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
class DryRunReport:
    tables: tuple[DryRunTableResult, ...]

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
    registry: DbmsAdapterRegistry | None = None,
) -> DryRunReport:
    adapter_registry = registry or default_adapter_registry()
    generator = adapter_registry.create_ddl_generator(target_dbms, target_database=target_database)
    return DryRunReport(tables=tuple(_build_table_result(generator, table) for table in snapshot.tables))


def write_dry_run_report(report: DryRunReport, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_json(report, output_dir / "summary.json")
    _write_csv(report, output_dir / "tables.csv")
    _write_html(report, output_dir / "summary.html")


def _build_table_result(generator: DdlGenerator, table: TableSchema) -> DryRunTableResult:
    ddl_result: DdlResult = generator.generate_create_table(table)
    warnings = tuple(_build_warning(message) for message in ddl_result.warnings)
    return DryRunTableResult(
        schema=table.ref.schema,
        table=table.ref.name,
        ddl=ddl_result.ddl,
        warning_count=len(warnings),
        warnings=warnings,
    )


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
            <td colspan="5" class="empty-state">No warnings. DDL can move to apply review.</td>
          </tr>
        """
    table_rows = "\n".join(_table_row(table) for table in report.tables)
    html = f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <title>DB Migrator Dry-run Report</title>
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
        <div class="eyebrow">Dry-run Report</div>
        <h1>DB Migrator Dry-run Report</h1>
        <p>Target DDL을 실행하기 전에 schema 변환 위험과 조치사항을 검토하는 리포트입니다.</p>
      </div>
      <div class="summary-grid">
        <div class="metric"><span>Total tables</span><strong>{report.table_count}</strong></div>
        <div class="metric"><span>Total warnings</span><strong>{report.warning_count}</strong></div>
        <div class="metric"><span>Next step</span><strong>{_next_step_label(report)}</strong></div>
      </div>
    </header>

    <section>
      <h2>Warnings & Recommended Actions</h2>
      <table>
        <thead>
          <tr>
            <th style="width: 14%;">schema</th>
            <th style="width: 18%;">table</th>
            <th style="width: 12%;">severity</th>
            <th style="width: 28%;">warning</th>
            <th style="width: 28%;">recommended action</th>
          </tr>
        </thead>
        <tbody>
{warning_rows}
        </tbody>
      </table>
    </section>

    <section>
      <h2>Table DDL Preview</h2>
      <table>
        <thead>
          <tr>
            <th style="width: 18%;">schema</th>
            <th style="width: 22%;">table</th>
            <th style="width: 14%;">warnings</th>
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
        message=message,
        severity=_warning_severity(normalized_message),
        action=_warning_action(normalized_message),
    )


def _warning_severity(normalized_message: str) -> str:
    if "manual review" in normalized_message or "unknown" in normalized_message or "array" in normalized_message:
        return "manual review"
    return "review"


def _warning_action(normalized_message: str) -> str:
    if "timestamp with time zone" in normalized_message or "timestamptz" in normalized_message:
        return (
            "Confirm the source timezone policy. If values must preserve absolute time, normalize to UTC before or "
            "during migration and validate samples after load."
        )
    if "jsonb" in normalized_message:
        return (
            "Review JSON query/index behavior used by the application. MySQL JSON stores valid JSON, but PostgreSQL "
            "jsonb operators and indexes are not equivalent."
        )
    if "uuid" in normalized_message:
        return "Confirm char(36) is acceptable for UUID storage and review primary key or index size/performance."
    if "generated column" in normalized_message:
        return "Rewrite and test the generated column expression for MySQL/MariaDB before running apply-ddl."
    if "array" in normalized_message:
        return "Choose an explicit target model, such as JSON text or a child table, before running apply-ddl."
    if "requires manual review" in normalized_message:
        return "Define an explicit type mapping or custom migration rule before running apply-ddl."
    return "Review the generated DDL and confirm application behavior before running apply-ddl."


def _next_step_label(report: DryRunReport) -> str:
    if report.warning_count:
        return "Review"
    return "Apply DDL"


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
        f'<span class="badge warning">{table.warning_count} warning</span>'
        if table.warning_count
        else '<span class="badge ok">clear</span>'
    )
    return f"""
          <tr>
            <td>{escape(table.schema)}</td>
            <td>{escape(table.table)}</td>
            <td>{warning_badge}</td>
            <td>
              <details>
                <summary>View CREATE TABLE</summary>
                <pre>{escape(table.ddl)}</pre>
              </details>
            </td>
          </tr>
    """
