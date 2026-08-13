import json
from pathlib import Path

from db_migrator.reports.dry_run import DryRunMetadata, build_dry_run_report, write_dry_run_report
from db_migrator.reports.metadata import ReportEndpoint
from db_migrator.schema.common_types import CommonType, CommonTypeKind, TypePolicy
from db_migrator.schema.models import ColumnSchema, IndexSchema, PrimaryKey, SchemaObjectKind, SchemaObjectSummary, SchemaSnapshot, TableRef, TableSchema
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
            migration_mode="ddl_and_dml",
            existing_table_policy="overwrite",
        ),
    )

    write_dry_run_report(report, tmp_path)

    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert len(summary["tables"]) == 2
    assert "CREATE TABLE `target_db`.`users`" in summary["tables"][0]["ddl"]
    assert "CREATE TABLE \"public\".\"users\"" in summary["tables"][0]["source_ddl"]
    assert "\"created_at\" timestamp with time zone NOT NULL" in summary["tables"][0]["source_ddl"]
    assert "CREATE TABLE `target_db`.`users`" in summary["tables"][0]["target_ddl"]
    assert summary["tables"][0]["warnings"][0]["message"] == "PostgreSQL jsonb는 MySQL JSON과 의미/인덱스 동작이 달라 검토가 필요합니다."
    assert "JSON query/index 사용 방식" in summary["tables"][0]["warnings"][0]["action"]
    assert summary["metadata"]["source"]["database"] == "source_db"
    assert summary["metadata"]["target"]["database"] == "target_db"

    html = (tmp_path / "summary.html").read_text(encoding="utf-8")
    assert "Jigration 사전 점검 리포트" in html
    assert "이관 정보" in html
    assert "원본 DB" in html
    assert "대상 DB" in html
    assert "사전 점검 기준" in html
    assert "2026-08-10 12:00:00" in html
    assert "2026-08-10T12:00:00+09:00" not in html
    assert "source.local:5432" in html
    assert "source_db" in html
    assert "target.local:3306" in html
    assert "target_db" in html
    assert "테이블 생성 SQL 실행" in html
    assert "실행 안 함" in html
    assert "기본 이관" in html
    assert "덮어쓰기" in html
    assert "기존 테이블 삭제 후 재생성" not in html
    assert "ddl_and_dml" not in html
    assert "overwrite" not in html
    assert "not_executed" not in html
    assert "검토 필요 항목 및 권장 조치" in html
    assert "총 테이블 수" in html
    assert "다음 단계" not in html
    assert "검토 필요 항목 수" in html
    assert "수동 검토 객체 수" in html
    assert "object-count-metric" not in html
    assert "지원 외 객체 수" not in html
    assert "PostgreSQL timestamp with time zone은 MySQL datetime으로 변환되므로 timezone 검토가 필요합니다." in html
    assert "기준 timezone으로 정규화" in html
    assert "수동 검토 객체" in html
    assert "인덱스 생성 계획" in html
    assert "원본 / 대상 테이블 생성 SQL 비교" in html
    assert "원본 CREATE TABLE" in html
    assert "대상 CREATE TABLE" in html
    assert "Dry-run" not in html
    assert "AS-IS" not in html
    assert "TO-BE" not in html
    assert 'colspan="4"' in html
    assert 'class="ddl-summary"' in html

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


def test_dry_run_report_shows_auto_increment_only_in_target_ddl() -> None:
    table = TableSchema(
        ref=TableRef(schema="public", name="privacy_body"),
        columns=(
            ColumnSchema(
                name="id",
                source_type="bigint",
                common_type=CommonType(kind=CommonTypeKind.BIGINT, policy=TypePolicy.AUTO_CONVERT),
                nullable=False,
                default="nextval('privacy_body_id_seq'::regclass)",
                is_generated=False,
                generation_expression=None,
                ordinal_position=1,
                auto_increment=True,
            ),
        ),
        primary_key=PrimaryKey(columns=("id",)),
    )

    report = build_dry_run_report(SchemaSnapshot(tables=(table,)), target_database="target_db")

    assert "AUTO_INCREMENT" in report.tables[0].target_ddl
    assert report.tables[0].warnings == ()


def test_dry_run_report_checks_unsupported_schema_objects() -> None:
    snapshot = SchemaSnapshot(
        tables=(
            TableSchema(
                ref=TableRef(schema="public", name="events"),
                columns=(
                    ColumnSchema(
                        name="id",
                        source_type="integer",
                        common_type=CommonType(kind=CommonTypeKind.INTEGER, policy=TypePolicy.AUTO_CONVERT),
                        nullable=False,
                        default=None,
                        is_generated=False,
                        generation_expression=None,
                        ordinal_position=1,
                    ),
                ),
                indexes=(IndexSchema(name="idx_events_id", columns=("id",)),),
            ),
        ),
        non_table_objects=(
            SchemaObjectSummary(kind=SchemaObjectKind.VIEW, schema="public", name="event_view"),
            SchemaObjectSummary(kind=SchemaObjectKind.FUNCTION, schema="public", name="normalize_event"),
            SchemaObjectSummary(kind=SchemaObjectKind.TRIGGER, schema="public", name="events_audit_trigger"),
        ),
    )

    report = build_dry_run_report(snapshot, target_database="target_db")

    checks = {check.object_type: check for check in report.object_checks}
    assert checks["인덱스"].count == 0
    assert checks["뷰"].count == 1
    assert checks["함수"].count == 1
    assert checks["트리거"].count == 1
    assert checks["프로시저"].count == 0
    assert report.index_plan[0].timing == "post_data"
    assert "CREATE INDEX" in (report.index_plan[0].ddl or "")


def test_dry_run_report_marks_complex_index_for_manual_review() -> None:
    table = TableSchema(
        ref=TableRef(schema="public", name="events"),
        columns=(
            ColumnSchema(
                name="payload",
                source_type="jsonb",
                common_type=CommonType(kind=CommonTypeKind.JSON, policy=TypePolicy.WARN_CONVERT),
                nullable=True,
                default=None,
                is_generated=False,
                generation_expression=None,
                ordinal_position=1,
            ),
        ),
        indexes=(
            IndexSchema(
                name="idx_events_payload_expr",
                columns=(),
                auto_create_candidate=False,
                manual_review_reason="Expression index requires manual conversion.",
            ),
        ),
    )

    report = build_dry_run_report(SchemaSnapshot(tables=(table,)), target_database="target_db")

    checks = {check.object_type: check for check in report.object_checks}
    assert checks["인덱스"].count == 1
    assert report.index_plan[0].timing == "manual_review"
    assert report.index_plan[0].ddl is None
