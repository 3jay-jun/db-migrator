import json
from pathlib import Path

from db_migrator.reports.dry_run import build_dry_run_report, write_dry_run_report
from db_migrator.schema.snapshot_io import load_schema_snapshot_from_json


def test_dry_run_report_writes_summary_files(tmp_path) -> None:
    snapshot = load_schema_snapshot_from_json(Path("tests/fixtures/schema_snapshot.json"))
    report = build_dry_run_report(snapshot, target_database="target_db")

    write_dry_run_report(report, tmp_path)

    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert len(summary["tables"]) == 2
    assert "CREATE TABLE `target_db`.`users`" in summary["tables"][0]["ddl"]
    assert summary["tables"][0]["warnings"][0]["message"] == "PostgreSQL jsonb semantic differences require review."
    assert "Review JSON query/index behavior" in summary["tables"][0]["warnings"][0]["action"]

    html = (tmp_path / "summary.html").read_text(encoding="utf-8")
    assert "Warnings & Recommended Actions" in html
    assert "PostgreSQL timestamp with time zone is converted to MySQL datetime" in html
    assert "normalize to UTC" in html

    csv_text = (tmp_path / "tables.csv").read_text(encoding="utf-8")
    assert "recommended_actions" in csv_text
    assert (tmp_path / "tables.csv").exists()
    assert (tmp_path / "summary.html").exists()
