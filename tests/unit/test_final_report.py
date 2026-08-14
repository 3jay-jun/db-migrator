import json
from dataclasses import replace
from pathlib import Path

from db_migrator.config.models import VerificationConfig
from db_migrator.core.validation import DataSyncArtifact, ExecutionArtifact, ValidationEndpoint, ValidationMetadata, validate_tables
from db_migrator.reports.final_report import write_validation_report
from db_migrator.schema.models import ForeignKeySchema, IndexSchema, SchemaObjectKind, SchemaObjectSummary, SchemaSnapshot, TableRef
from db_migrator.schema.snapshot_io import load_schema_snapshot_from_json


class MatchingReader:
    def count_rows(self, side: str, table) -> int:
        return 1

    def sample_rows(self, side: str, table_schema, sample_size: int) -> tuple[dict, ...]:
        return ({"id": 1},)


class MismatchingReader:
    def count_rows(self, side: str, table) -> int:
        return 2 if side == "source" else 1

    def sample_rows(self, side: str, table_schema, sample_size: int) -> tuple[dict, ...]:
        if side == "source":
            return ({"id": 1, "email": "source@example.com"},)
        return ({"id": 1, "email": "target@example.com"},)


class FailingValidationReader:
    def count_rows(self, side: str, table) -> int:
        raise RuntimeError(f"{side} row count unavailable")

    def sample_rows(self, side: str, table_schema, sample_size: int) -> tuple[dict, ...]:
        raise RuntimeError(f"{side} sample unavailable")


def test_write_validation_report_outputs_json_csv_html_and_errors(tmp_path: Path) -> None:
    snapshot = load_schema_snapshot_from_json(Path("tests/fixtures/schema_snapshot.json"))
    report = validate_tables(
        job_id="job-1",
        tables=(snapshot.tables[0],),
        reader=MatchingReader(),
        verification=VerificationConfig(),
        metadata=ValidationMetadata(
            generated_at="2026-08-10T03:00:00+00:00",
            source=ValidationEndpoint(
                dbms="postgresql",
                host="source.local",
                port=5432,
                database="source_db",
                schema="public",
            ),
            target=ValidationEndpoint(
                dbms="mysql",
                host="target.local",
                port=3306,
                database="target_db",
            ),
            migration_mode="ddl_and_dml",
            existing_table_policy="overwrite",
            checksum_sample_size=100,
            checksum_timezone="Asia/Seoul",
            checksum_datetime_precision="microseconds",
        ),
    )

    write_validation_report(report, tmp_path)

    assert (tmp_path / "summary.json").exists()
    assert (tmp_path / "tables.csv").exists()
    assert (tmp_path / "errors.csv").exists()
    assert (tmp_path / "differences.csv").exists()

    html = (tmp_path / "summary.html").read_text(encoding="utf-8")
    assert "Jigration 검증 리포트" in html
    assert "--color-primary: oklch(0.000 0.000 0)" in html
    assert "이슈 및 권장 조치" in html
    assert "검산 샘플" in html
    assert "원본 값" in html
    assert "대상 값" in html
    assert "검증 결과" in html
    assert "성공" in html
    assert "검증 테이블 수" in html
    assert "전체 데이터 수" in html
    assert "검증 데이터 수" in html
    assert "이관 테이블 수" not in html
    assert "이관 데이터 수" not in html
    assert "총 테이블 수" not in html
    assert "총 이관 수" not in html
    assert "이슈 수" in html
    assert "이슈 테이블 수" not in html
    assert "스키마 객체 이슈 수" not in html
    assert "실행 산출물 수" not in html
    assert "테이블별 검증 요약" in html
    assert "검증 수행 내역" in html
    assert "행 수 검증" in html
    assert "검산 샘플 검증" in html
    assert "DDL/스키마 검증" in html
    assert "source 후보 객체 vs target 실제 객체" in html
    assert "행 수 차이" in html
    assert "대표 이슈" in html
    assert "다음 조치" in html
    assert "샘플 값 차이 수" not in html
    assert "샘플 값 검증" not in html
    assert "<h2>검산 샘플</h2>" not in html
    assert "작업 ID" not in html
    assert "전체 상태" not in html
    assert "총 원본 행 수" not in html
    assert "총 매칭 행 수" not in html
    assert "매칭 행 수" not in html
    assert "값 차이/정상 샘플" not in html
    assert "검증 대상 및 기준" in html
    assert "작업 식별자" not in html
    assert "2026-08-10 12:00:00" in html
    assert "2026-08-10T03:00:00+00:00" not in html
    assert "실행 방식" in html
    assert "기본 이관" in html
    assert "기존 테이블 처리" in html
    assert "덮어쓰기" in html
    assert "일시 비교 정밀도" in html
    assert "마이크로초" in html
    assert "행 수 비교" not in html
    assert "검산 샘플 비교" not in html
    assert "검산 샘플 수" not in html
    assert "<dt>검증 테이블</dt>" not in html
    assert "<dt>이슈 테이블</dt>" not in html
    assert "<dt>테이블 상태</dt>" not in html
    assert "POSTGRESQL" in html
    assert "source.local:5432" in html
    assert "source_db" in html
    assert "MYSQL" in html
    assert "target.local:3306" in html
    assert "target_db" in html
    assert "Asia/Seoul" in html
    assert "id=1" in html
    assert "테이블 관련 이슈 및 조치사항이 없습니다." in html
    assert "스키마 객체 관련 이슈 및 조치사항이 없습니다." in html

    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert summary["metadata"]["source"]["database"] == "source_db"
    assert summary["metadata"]["target"]["database"] == "target_db"
    assert summary["summary"]["total_source_rows"] == 1
    assert summary["summary"]["total_target_rows"] == 1
    assert summary["summary"]["total_matched_rows"] == 1
    assert summary["summary"]["successful_table_count"] == 1
    assert summary["summary"]["successful_migrated_rows"] == 1
    assert summary["summary"]["issue_count"] == 0
    assert summary["summary"]["total_error_count"] == 0
    assert summary["table_summaries"][0]["matched_rows"] == 1
    assert summary["tables"][0]["checksum"]["matched_samples"][0]["row_identity"] == "id=1"

    tables_csv = (tmp_path / "tables.csv").read_text(encoding="utf-8")
    assert "매칭_행수" in tables_csv
    assert "오류_수" in tables_csv
    assert "값차이_수" in tables_csv


def test_write_validation_report_includes_issue_actions(tmp_path: Path) -> None:
    snapshot = load_schema_snapshot_from_json(Path("tests/fixtures/schema_snapshot.json"))
    report = validate_tables(
        job_id="job-1",
        tables=(snapshot.tables[0],),
        reader=MismatchingReader(),
        verification=VerificationConfig(),
    )

    write_validation_report(report, tmp_path)

    html = (tmp_path / "summary.html").read_text(encoding="utf-8")
    assert "행 수" in html
    assert "검산 샘플" in html
    assert "colspan=\"6\"" in html
    assert "issue-summary" in html
    assert "difference-panel" in html
    assert "sample row" not in html
    assert "Table Validation Results" not in html
    assert "email" in html
    assert "source@example.com" in html
    assert "target@example.com" in html
    assert "대상에 1행이 부족합니다." in html
    assert "email의 값 변환을 확인하세요." in html
    assert "테이블별 검증 요약" in html
    assert "대표 이슈" in html
    assert "다음 조치" in html
    assert "대상 1행 부족" in html
    assert "2" in html
    assert "1" in html
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert summary["summary"]["successful_table_count"] == 0
    assert summary["summary"]["table_count"] == 1
    assert summary["summary"]["total_target_rows"] == 1
    assert summary["summary"]["successful_migrated_rows"] == 0
    assert summary["summary"]["issue_count"] == 1

    differences_csv = (tmp_path / "differences.csv").read_text(encoding="utf-8")
    assert "행_식별값" in differences_csv
    assert "row_number" not in differences_csv
    assert "source@example.com" in differences_csv
    assert "target@example.com" in differences_csv


def test_write_validation_report_includes_schema_object_mismatches(tmp_path: Path) -> None:
    snapshot = load_schema_snapshot_from_json(Path("tests/fixtures/schema_snapshot.json"))
    source_table = snapshot.tables[0]
    expected = SchemaSnapshot(
        tables=(
            source_table.__class__(
                ref=source_table.ref,
                columns=source_table.columns,
                primary_key=source_table.primary_key,
                indexes=(
                    IndexSchema(name="idx_users_email", columns=("email",), unique=True, method="btree"),
                    IndexSchema(
                        name="idx_users_profile_expr",
                        columns=(),
                        auto_create_candidate=False,
                        manual_review_reason="Expression index requires manual conversion.",
                    ),
                ),
            ),
        ),
        non_table_objects=(SchemaObjectSummary(kind=SchemaObjectKind.VIEW, schema="public", name="active_users"),),
    )
    actual = SchemaSnapshot(tables=(source_table,), non_table_objects=())
    report = validate_tables(
        job_id="job-1",
        tables=(source_table,),
        reader=MatchingReader(),
        verification=VerificationConfig(),
        source_snapshot=expected,
        target_snapshot=actual,
    )

    write_validation_report(report, tmp_path)

    assert report.status == "mismatched"
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert summary["summary"]["schema_object_issue_count"] == 3
    assert summary["summary"]["issue_count"] == 3
    html = (tmp_path / "summary.html").read_text(encoding="utf-8")
    assert "스키마 객체 검증" in html
    assert "idx_users_email" in html
    assert "idx_users_profile_expr" in html
    assert "active_users" in html
    assert "테이블" in html
    assert "컬럼" in html
    assert "PK" in html
    assert "source 후보" in html
    assert "target 실제" in html
    assert "누락" in html
    assert "수동 검토" in html
    assert "Expression index requires manual conversion." in html
    schema_objects_csv = (tmp_path / "schema-objects.csv").read_text(encoding="utf-8")
    assert "source_후보" in schema_objects_csv
    assert "target_실제" in schema_objects_csv
    assert "idx_users_email" in schema_objects_csv
    assert "idx_users_profile_expr" in schema_objects_csv
    assert "active_users" in schema_objects_csv


def test_write_validation_report_flags_missing_auto_increment_column_property(tmp_path: Path) -> None:
    snapshot = load_schema_snapshot_from_json(Path("tests/fixtures/schema_snapshot.json"))
    source_table = snapshot.tables[0]
    expected_id = replace(source_table.columns[0], auto_increment=True)
    expected = SchemaSnapshot(tables=(replace(source_table, columns=(expected_id, *source_table.columns[1:])),))
    actual_id = replace(source_table.columns[0], auto_increment=False)
    actual = SchemaSnapshot(tables=(replace(source_table, columns=(actual_id, *source_table.columns[1:])),))

    report = validate_tables(
        job_id="job-1",
        tables=(source_table,),
        reader=MatchingReader(),
        verification=VerificationConfig(),
        source_snapshot=expected,
        target_snapshot=actual,
    )

    write_validation_report(report, tmp_path)

    assert report.status == "mismatched"
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert summary["summary"]["schema_object_issue_count"] == 1
    assert summary["summary"]["issue_count"] == 1
    schema_objects_csv = (tmp_path / "schema-objects.csv").read_text(encoding="utf-8")
    assert "컬럼 속성" in schema_objects_csv
    assert "AUTO_INCREMENT 누락" in schema_objects_csv


def test_write_validation_report_keeps_target_only_schema_objects_informational(tmp_path: Path) -> None:
    snapshot = load_schema_snapshot_from_json(Path("tests/fixtures/schema_snapshot.json"))
    source_table = snapshot.tables[0]
    target_only_table = replace(source_table, ref=TableRef(schema="public", name="user_log"))
    report = validate_tables(
        job_id="job-1",
        tables=(source_table,),
        reader=MatchingReader(),
        verification=VerificationConfig(),
        source_snapshot=SchemaSnapshot(tables=(source_table,)),
        target_snapshot=SchemaSnapshot(tables=(source_table, target_only_table)),
    )

    write_validation_report(report, tmp_path)

    assert report.status == "matched"
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert summary["summary"]["schema_object_issue_count"] == 0
    assert summary["summary"]["schema_object_warning_count"] == 1
    assert summary["summary"]["warning_count"] == 1
    assert summary["summary"]["issue_count"] == 0
    html = (tmp_path / "summary.html").read_text(encoding="utf-8")
    assert '<div class="metric"><span>경고 수</span><strong>1</strong></div>' in html
    schema_objects_csv = (tmp_path / "schema-objects.csv").read_text(encoding="utf-8")
    assert "public.user_log" in schema_objects_csv
    assert "target_only" in schema_objects_csv


def test_write_validation_report_includes_execution_artifacts(tmp_path: Path) -> None:
    snapshot = load_schema_snapshot_from_json(Path("tests/fixtures/schema_snapshot.json"))
    report = validate_tables(
        job_id="job-1",
        tables=(snapshot.tables[0],),
        reader=MatchingReader(),
        verification=VerificationConfig(),
        execution_artifacts=(
            ExecutionArtifact(
                artifact_type="DDL",
                object_name="target_db.users",
                action="create",
                success=True,
                message="ok",
                ddl="CREATE TABLE `target_db`.`users` (`id` int);",
                source_file="ddl-execution.json",
            ),
            ExecutionArtifact(
                artifact_type="INDEX",
                object_name="target_db.users.idx_users_email",
                action="post_data",
                success=True,
                message="ok",
                ddl="CREATE INDEX `idx_users_email` ON `target_db`.`users` (`email`);",
                source_file="index-execution-post_data.json",
            ),
            ExecutionArtifact(
                artifact_type="FK",
                object_name="target_db.orders.orders_user_id_fkey",
                action="add_constraint",
                success=False,
                message="missing parent table",
                ddl="ALTER TABLE `target_db`.`orders` ADD CONSTRAINT `orders_user_id_fkey` FOREIGN KEY (`user_id`) REFERENCES `target_db`.`users` (`id`);",
                source_file="foreign-key-execution.json",
            ),
        ),
    )

    write_validation_report(report, tmp_path)

    assert report.status == "failed"
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert summary["summary"]["execution_artifact_count"] == 3
    assert summary["summary"]["failed_execution_artifact_count"] == 1
    assert summary["summary"]["issue_count"] == 1
    assert summary["execution_artifacts"][0]["ddl"].startswith("CREATE TABLE")
    html = (tmp_path / "summary.html").read_text(encoding="utf-8")
    assert "실행 산출물 수" not in html
    assert "실제 작업 내역" in html
    assert "target_db.users.idx_users_email" in html
    assert "missing parent table" in html
    assert "비교 결과" in html
    assert "실행 SQL" in html
    assert "펼쳐보기" in html
    assert "원본 파일" not in html
    execution_artifacts_csv = (tmp_path / "execution-artifacts.csv").read_text(encoding="utf-8")
    assert "CREATE INDEX" in execution_artifacts_csv
    assert "missing parent table" in execution_artifacts_csv


def test_write_validation_report_marks_data_metrics_not_applicable_for_ddl_only(tmp_path: Path) -> None:
    snapshot = load_schema_snapshot_from_json(Path("tests/fixtures/schema_snapshot.json"))
    report = validate_tables(
        job_id="job-1",
        tables=(snapshot.tables[0],),
        reader=MatchingReader(),
        verification=VerificationConfig(),
        metadata=ValidationMetadata(
            generated_at="2026-08-10T03:00:00+00:00",
            migration_mode="ddl_only",
            existing_table_policy="sync",
        ),
    )

    write_validation_report(report, tmp_path)

    html = (tmp_path / "summary.html").read_text(encoding="utf-8")
    assert '<div class="metric"><span>전체 데이터 수</span><strong>-</strong></div>' in html
    assert '<div class="metric"><span>검증 데이터 수</span><strong>-</strong></div>' in html


def test_write_validation_report_uses_changed_sync_rows_as_summary_scope(tmp_path: Path) -> None:
    snapshot = load_schema_snapshot_from_json(Path("tests/fixtures/schema_snapshot.json"))
    report = validate_tables(
        job_id="job-1",
        tables=snapshot.tables,
        reader=MatchingReader(),
        verification=VerificationConfig(),
        metadata=ValidationMetadata(
            generated_at="2026-08-10T03:00:00+00:00",
            migration_mode="ddl_and_dml",
            existing_table_policy="sync",
        ),
        data_sync_artifacts=(
            DataSyncArtifact(
                schema="public",
                table="users",
                status="completed",
                rows_inserted=1,
                rows_updated=1,
                rows_deleted=1,
                rows_unchanged=10,
                rows_processed=13,
                rows_written=2,
                changed_rows=3,
                batches_written=1,
            ),
            DataSyncArtifact(
                schema="public",
                table="orders",
                status="completed",
                rows_inserted=0,
                rows_updated=0,
                rows_deleted=0,
                rows_unchanged=20,
                rows_processed=20,
                rows_written=0,
                changed_rows=0,
                batches_written=1,
            ),
        ),
    )

    write_validation_report(report, tmp_path)

    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert summary["summary"]["table_count"] == 1
    assert summary["summary"]["total_target_rows"] == 3
    assert summary["summary"]["successful_migrated_rows"] == 3
    html = (tmp_path / "summary.html").read_text(encoding="utf-8")
    assert '<div class="metric"><span>검증 테이블 수</span><strong>1</strong></div>' in html
    assert '<div class="metric"><span>전체 데이터 수</span><strong>3</strong></div>' in html
    assert '<div class="metric"><span>검증 데이터 수</span><strong>3</strong></div>' in html
    assert "추가 1, 수정 1, 삭제 1" in html
    assert "이관 데이터 검증" in html
    assert "<td>3</td>" in html
    assert "insert/update 대상 row를 target 반영 후 비교" in html
    assert "id=1" not in html
    assert "orders" not in (tmp_path / "tables.csv").read_text(encoding="utf-8")


def test_write_validation_report_keeps_failed_validation_counts_separate_from_sync_rows(tmp_path: Path) -> None:
    snapshot = load_schema_snapshot_from_json(Path("tests/fixtures/schema_snapshot.json"))
    table_name = snapshot.tables[0].ref.name
    report = validate_tables(
        job_id="job-1",
        tables=(snapshot.tables[0],),
        reader=FailingValidationReader(),
        verification=VerificationConfig(),
        data_sync_artifacts=(
            DataSyncArtifact(
                schema="public",
                table=table_name,
                status="completed",
                rows_inserted=17,
                rows_updated=0,
                rows_deleted=0,
                rows_unchanged=0,
                rows_processed=17,
                rows_written=17,
                changed_rows=17,
                batches_written=1,
            ),
        ),
    )

    write_validation_report(report, tmp_path)

    html = (tmp_path / "summary.html").read_text(encoding="utf-8")
    assert "source row count unavailable" in html
    assert "이관 데이터 검증" in html
    assert "<td>17</td>" in html
    assert "<span>-</span>" in html
    tables_csv = (tmp_path / "tables.csv").read_text(encoding="utf-8")
    assert "failed,,,failed,failed" in tables_csv


def test_write_validation_report_shows_zero_scope_when_sync_has_no_changes(tmp_path: Path) -> None:
    snapshot = load_schema_snapshot_from_json(Path("tests/fixtures/schema_snapshot.json"))
    report = validate_tables(
        job_id="job-1",
        tables=(snapshot.tables[0],),
        reader=MatchingReader(),
        verification=VerificationConfig(),
        data_sync_artifacts=(
            DataSyncArtifact(
                schema="public",
                table="users",
                status="completed",
                rows_inserted=0,
                rows_updated=0,
                rows_deleted=0,
                rows_unchanged=1,
                rows_processed=1,
                rows_written=0,
                changed_rows=0,
                batches_written=1,
            ),
        ),
    )

    write_validation_report(report, tmp_path)

    html = (tmp_path / "summary.html").read_text(encoding="utf-8")
    assert '<div class="metric"><span>검증 테이블 수</span><strong>0</strong></div>' in html
    assert '<div class="metric"><span>전체 데이터 수</span><strong>0</strong></div>' in html
    assert '<div class="metric"><span>검증 데이터 수</span><strong>0</strong></div>' in html
    assert "이번 실행에서 검증할 이관/수정/삭제 테이블이 없습니다." in html


def test_write_validation_report_compares_foreign_key_objects(tmp_path: Path) -> None:
    snapshot = load_schema_snapshot_from_json(Path("tests/fixtures/schema_snapshot.json"))
    users = snapshot.tables[0]
    orders = replace(
        users,
        ref=TableRef(schema="public", name="orders"),
        foreign_keys=(
            ForeignKeySchema(
                name="orders_user_id_fkey",
                columns=("user_id",),
                referenced_table=TableRef(schema="public", name="users"),
                referenced_columns=("id",),
            ),
        ),
    )
    expected = SchemaSnapshot(tables=(users, orders))
    actual = SchemaSnapshot(tables=(users, replace(orders, foreign_keys=())))

    report = validate_tables(
        job_id="job-1",
        tables=(users,),
        reader=MatchingReader(),
        verification=VerificationConfig(),
        source_snapshot=expected,
        target_snapshot=actual,
    )

    write_validation_report(report, tmp_path)

    assert report.status == "mismatched"
    html = (tmp_path / "summary.html").read_text(encoding="utf-8")
    assert "orders_user_id_fkey" in html
    assert "apply-foreign-keys" in html
