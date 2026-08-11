from __future__ import annotations

import csv
import json
from dataclasses import asdict
from html import escape
from pathlib import Path

from db_migrator.core.incremental import IncrementalMigrationReport
from db_migrator.reports.labels import result_label, status_label


def write_incremental_report(report: IncrementalMigrationReport, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "summary.json").write_text(
        json.dumps(asdict(report), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _write_tables_csv(report, output_dir / "tables.csv")
    _write_html(report, output_dir / "summary.html")
    _write_delete_policy(report, output_dir / "delete_policy.txt")


def _write_tables_csv(report: IncrementalMigrationReport, output_path: Path) -> None:
    with output_path.open("w", encoding="utf-8", newline="") as csv_file:
        writer = csv.writer(csv_file)
        writer.writerow(["schema", "table", "status", "rows_upserted", "batches_upserted", "watermark_column", "message"])
        for table in report.tables:
            writer.writerow(
                [
                    table.table.schema,
                    _table_label(table),
                    table.status,
                    table.rows_upserted,
                    table.batches_upserted,
                    table.watermark_column,
                    table.message,
                ]
            )


def _write_html(report: IncrementalMigrationReport, output_path: Path) -> None:
    rows = "\n".join(_table_row(table) for table in report.tables)
    if not rows:
        rows = """
          <tr>
            <td colspan="7" class="empty-state">증분 이관 대상 테이블이 없습니다.</td>
          </tr>
        """
    html = f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <title>Jigration 증분 이관 리포트</title>
  <style>
    :root {{
      color-scheme: light;
      --color-canvas: oklch(1.000 0.000 0);
      --color-canvas-200: oklch(0.976 0.000 0);
      --color-primary: oklch(0.000 0.000 0);
      --color-text-strong: oklch(0.269 0.000 0);
      --color-text-normal: oklch(0.417 0.000 0);
      --color-text-muted: oklch(0.478 0.000 0);
      --color-border: oklch(0.827 0.000 0);
      --color-success-soft: oklch(0.974 0.016 167);
      --color-success-text: oklch(0.407 0.090 162);
      --color-warning-soft: oklch(0.979 0.012 51);
      --color-warning-text: oklch(0.503 0.188 33);
      --color-warning-border: oklch(0.836 0.092 46);
      --color-danger-soft: oklch(0.978 0.011 24);
      --color-danger-text: oklch(0.505 0.196 24);
      --color-danger-border: oklch(0.838 0.089 20);
      --space-050: 4px;
      --space-100: 8px;
      --space-150: 12px;
      --space-200: 16px;
      --space-300: 24px;
      --space-400: 32px;
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
    }}
    h2 {{
      margin: 0 0 var(--space-200);
      font-size: 18px;
      line-height: 26px;
      font-weight: 700;
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
    }}
    .summary-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: var(--space-150);
    }}
    .metric, section {{
      background: var(--color-canvas);
      border: 1px solid var(--color-border);
      border-radius: var(--radius-400);
      padding: var(--space-200);
    }}
    section {{
      padding: var(--space-300);
      margin-top: var(--space-200);
    }}
    .metric span {{
      display: block;
      color: var(--color-text-muted);
      font-size: 13px;
      line-height: 20px;
      margin-bottom: 6px;
    }}
    .metric strong {{
      display: block;
      color: var(--color-primary);
      font-size: 32px;
      line-height: 40px;
      font-weight: 800;
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
    }}
    .badge {{
      display: inline-flex;
      min-height: 22px;
      align-items: center;
      border-radius: var(--radius-circle);
      padding: 2px 8px;
      background: var(--color-primary);
      color: white;
      font-size: 12px;
      font-weight: 600;
    }}
    .badge.completed {{
      background: var(--color-success-soft);
      color: var(--color-success-text);
    }}
    .badge.skipped {{
      background: var(--color-warning-soft);
      color: var(--color-warning-text);
      border: 1px solid var(--color-warning-border);
    }}
    .badge.failed {{
      background: var(--color-danger-soft);
      color: var(--color-danger-text);
      border: 1px solid var(--color-danger-border);
    }}
    .empty-state {{
      color: var(--color-text-muted);
      text-align: center;
    }}
  </style>
</head>
<body>
  <main>
    <header>
      <div>
        <div class="eyebrow">증분 이관 리포트</div>
        <h1>Jigration 증분 이관 리포트</h1>
        <p>이번 증분 실행에서 어떤 테이블에 몇 건이 반영됐고 무엇이 건너뛰어졌는지 확인하는 리포트입니다.</p>
      </div>
      <div class="summary-grid">
        <div class="metric"><span>증분 이관 결과</span><strong>{escape(result_label(_report_status(report)))}</strong></div>
        <div class="metric"><span>반영 행 수</span><strong>{report.rows_upserted}</strong></div>
        <div class="metric"><span>대상 테이블 수</span><strong>{len(report.tables)}</strong></div>
        <div class="metric"><span>건너뛴 테이블 수</span><strong>{_skipped_table_count(report)}</strong></div>
      </div>
    </header>

    <section>
      <h2>테이블별 증분 이관 결과</h2>
      <table>
        <thead>
          <tr>
            <th style="width: 12%;">스키마</th>
            <th style="width: 18%;">테이블명</th>
            <th style="width: 12%;">상태</th>
            <th style="width: 12%;">반영 행 수</th>
            <th style="width: 10%;">배치 수</th>
            <th style="width: 16%;">증분 기준 컬럼</th>
            <th style="width: 20%;">메시지</th>
          </tr>
        </thead>
        <tbody>
{rows}
        </tbody>
      </table>
    </section>

    <section>
      <h2>삭제 동기화 안내</h2>
      <p>{escape(_delete_policy_message())}</p>
    </section>
  </main>
</body>
</html>
"""
    output_path.write_text(html, encoding="utf-8")


def _write_delete_policy(report: IncrementalMigrationReport, output_path: Path) -> None:
    output_path.write_text(f"{_delete_policy_message()}\n", encoding="utf-8")


def _table_row(table) -> str:
    return f"""
          <tr>
            <td>{escape(table.table.schema)}</td>
            <td>{escape(_table_label(table))}</td>
            <td><span class="badge {escape(table.status)}">{escape(status_label(table.status))}</span></td>
            <td>{table.rows_upserted}</td>
            <td>{table.batches_upserted}</td>
            <td>{escape(table.watermark_column or "-")}</td>
            <td>{escape(table.message or "-")}</td>
          </tr>
    """


def _report_status(report: IncrementalMigrationReport) -> str:
    if any(table.status == "failed" for table in report.tables):
        return "failed"
    if any(table.status == "skipped" for table in report.tables):
        return "skipped"
    return "completed"


def _skipped_table_count(report: IncrementalMigrationReport) -> int:
    return sum(1 for table in report.tables if table.status == "skipped")


def _delete_policy_message() -> str:
    return "자동 삭제 동기화는 지원하지 않습니다. 삭제 대상은 별도 파일을 검토하세요."


def _table_label(table) -> str:
    source_label = f"{table.table.schema}.{table.table.name}"
    if table.target_table is None:
        return table.table.name
    target_label = f"{table.target_table.schema}.{table.target_table.name}"
    if source_label == target_label:
        return table.table.name
    return f"{source_label} -> {target_label}"
