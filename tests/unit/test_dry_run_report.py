import json
from pathlib import Path

from db_migrator.reports.dry_run import DryRunMetadata, build_dry_run_report, write_dry_run_report
from db_migrator.reports.metadata import ReportEndpoint
from db_migrator.schema.common_types import CommonType, CommonTypeKind, TypePolicy
from db_migrator.schema.models import ColumnSchema, SchemaSnapshot, TableRef, TableSchema
from db_migrator.schema.snapshot_io import load_schema_snapshot_from_json


def test_dry_run_report_writes_summary_files(tmp_path) -> None:
    snapshot = load_schema_snapshot_from_json(Path("tests/fixtures/schema_snapshot.json"))
    report = build_dry_run_report(
        snapshot,
        target_database="target_db",
        metadata=DryRunMetadata(
            generated_at="2026-08-10T12:00:00+09:00",
            source=ReportEndpoint(
                dbms="postgresql",
                host="source.local",
                port=5432,
                database="source_db",
                schema="public",
            ),
            target=ReportEndpoint(
                dbms="mysql",
                host="target.local",
                port=3306,
                database="target_db",
            ),
            migration_mode="full",
            existing_table_policy="fail",
        ),
    )

    write_dry_run_report(report, tmp_path)

    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert len(summary["tables"]) == 2
    assert "CREATE TABLE `target_db`.`users`" in summary["tables"][0]["ddl"]
    assert summary["tables"][0]["warnings"][0]["message"] == "PostgreSQL jsonb는 MySQL JSON과 의미/인덱스 동작이 달라 검토가 필요합니다."
    assert "JSON query/index 사용 방식" in summary["tables"][0]["warnings"][0]["action"]
    assert summary["metadata"]["source"]["database"] == "source_db"
    assert summary["metadata"]["target"]["database"] == "target_db"

    html = (tmp_path / "summary.html").read_text(encoding="utf-8")
    assert "DB Migrator Dry-run 리포트" in html
    assert "이관 정보" in html
    assert "원본 DB" in html
    assert "대상 DB" in html
    assert "Dry-run 기준" in html
    assert "source.local:5432" in html
    assert "source_db" in html
    assert "target.local:3306" in html
    assert "target_db" in html
    assert "DDL 실행 여부" in html
    assert "실행 안 함" in html
    assert "경고 및 권장 조치" in html
    assert "총 테이블 수" in html
    assert "다음 단계" in html
    assert "PostgreSQL timestamp with time zone은 MySQL datetime으로 변환되므로 timezone 검토가 필요합니다." in html
    assert "기준 timezone으로 정규화" in html
    assert "CREATE TABLE 보기" in html

    csv_text = (tmp_path / "tables.csv").read_text(encoding="utf-8")
    assert "recommended_actions" in csv_text
    assert (tmp_path / "tables.csv").exists()
    assert (tmp_path / "summary.html").exists()


def test_dry_run_report_warns_offset_fallback_for_table_without_unique_key() -> None:
    snapshot = SchemaSnapshot(
        tables=(
            TableSchema(
                ref=TableRef(schema="public", name="audit_logs"),
                columns=(
                    ColumnSchema(
                        name="message",
                        source_type="text",
                        common_type=CommonType(kind=CommonTypeKind.TEXT, policy=TypePolicy.AUTO_CONVERT),
                        nullable=False,
                        default=None,
                        is_generated=False,
                        generation_expression=None,
                        ordinal_position=1,
                    ),
                ),
            ),
        )
    )

    report = build_dry_run_report(snapshot, target_database="target_db")

    assert "offset 기준 resume" in report.tables[0].warnings[0].message
