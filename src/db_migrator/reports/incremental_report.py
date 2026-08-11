from __future__ import annotations

import csv
import json
from dataclasses import asdict
from pathlib import Path

from db_migrator.core.incremental import IncrementalMigrationReport


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
    rows = "\n".join(
        f"<tr><td>{table.table.schema}</td><td>{_table_label(table)}</td><td>{table.status}</td>"
        f"<td>{table.rows_upserted}</td><td>{table.watermark_column}</td></tr>"
        for table in report.tables
    )
    html = f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <title>Jigration Incremental Report</title>
</head>
<body>
  <h1>Jigration Incremental Report</h1>
  <p>job_id={report.job_id} rows_upserted={report.rows_upserted}</p>
  <p>delete_sync_supported={str(report.delete_sync_supported).lower()}</p>
  <table>
    <thead><tr><th>schema</th><th>table</th><th>status</th><th>rows</th><th>watermark</th></tr></thead>
    <tbody>
{rows}
    </tbody>
  </table>
</body>
</html>
"""
    output_path.write_text(html, encoding="utf-8")


def _write_delete_policy(report: IncrementalMigrationReport, output_path: Path) -> None:
    output_path.write_text(
        "DELETE sync is not supported automatically. Review and handle deletes manually.\n",
        encoding="utf-8",
    )


def _table_label(table) -> str:
    source_label = f"{table.table.schema}.{table.table.name}"
    if table.target_table is None:
        return table.table.name
    target_label = f"{table.target_table.schema}.{table.target_table.name}"
    if source_label == target_label:
        return table.table.name
    return f"{source_label} -> {target_label}"
