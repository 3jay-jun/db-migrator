import json
from pathlib import Path

from db_migrator.config.models import VerificationConfig
from db_migrator.core.validation import ValidationEndpoint, ValidationMetadata, validate_tables
from db_migrator.reports.final_report import write_validation_report
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


def test_write_validation_report_outputs_json_csv_html_and_errors(tmp_path: Path) -> None:
    snapshot = load_schema_snapshot_from_json(Path("tests/fixtures/schema_snapshot.json"))
    report = validate_tables(
        job_id="job-1",
        tables=(snapshot.tables[0],),
        reader=MatchingReader(),
        verification=VerificationConfig(),
        metadata=ValidationMetadata(
            generated_at="2026-08-10T12:00:00+09:00",
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
    assert "DB Migrator 검증 리포트" in html
    assert "--color-primary: oklch(0.000 0.000 0)" in html
    assert "이슈 및 권장 조치" in html
    assert "정상 이관 샘플" in html
    assert "이관 전 source" in html
    assert "이관 후 target" in html
    assert "총 테이블 수" in html
    assert "전체 상태" in html
    assert "검증 대상 및 기준" in html
    assert "POSTGRESQL" in html
    assert "source.local:5432" in html
    assert "source_db" in html
    assert "MYSQL" in html
    assert "target.local:3306" in html
    assert "target_db" in html
    assert "Asia/Seoul" in html
    assert "일치 1, 불일치 0, 실패 0, 건너뜀 0" in html
    assert "일치" in html
    assert "id=1" in html
    assert "검증 이슈가 없습니다. Source와 target이 일치합니다." in html

    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert summary["metadata"]["source"]["database"] == "source_db"
    assert summary["metadata"]["target"]["database"] == "target_db"
    assert summary["tables"][0]["checksum"]["matched_samples"][0]["row_identity"] == "id=1"


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
    assert "checksum" in html
    assert "colspan=\"6\"" in html
    assert "issue-summary" in html
    assert "difference-panel" in html
    assert "sample row" not in html
    assert "Table Validation Results" not in html
    assert "email" in html
    assert "source@example.com" in html
    assert "target@example.com" in html
    assert "Target에 1행이 부족합니다." in html
    assert "email의 값 변환을 확인하세요." in html

    differences_csv = (tmp_path / "differences.csv").read_text(encoding="utf-8")
    assert "row_identity" in differences_csv
    assert "row_number" not in differences_csv
    assert "source@example.com" in differences_csv
    assert "target@example.com" in differences_csv
