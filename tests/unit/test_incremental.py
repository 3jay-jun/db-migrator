from collections.abc import Iterator
from pathlib import Path

from db_migrator.config.loader import load_config
from db_migrator.config.models import IncrementalConfig, MigrationConfig, WatermarkConfig
from db_migrator.core.incremental import migrate_incremental_tables
from db_migrator.reports.incremental_report import write_incremental_report
from db_migrator.schema.models import ReadCursor, RowBatch, TableRef, WriteResult
from db_migrator.schema.snapshot_io import load_schema_snapshot_from_json


class FakeIncrementalSource:
    def __init__(self, rows_by_table: dict[str, list[dict]]) -> None:
        self.rows_by_table = rows_by_table
        self.calls: list[tuple[str, str, int]] = []

    def read_incremental_rows(
        self,
        table: TableRef,
        columns: tuple[str, ...],
        watermark: WatermarkConfig,
        batch_size: int,
    ) -> Iterator[RowBatch]:
        self.calls.append((table.name, watermark.column, batch_size))
        rows = self.rows_by_table.get(table.name, [])
        for index in range(0, len(rows), batch_size):
            chunk = tuple({column: row.get(column) for column in columns} for row in rows[index : index + batch_size])
            yield RowBatch(
                table=table,
                rows=chunk,
                batch_number=index // batch_size + 1,
                start_offset=index,
                next_cursor=ReadCursor(offset=index + len(chunk)),
            )


class FakeIncrementalTarget:
    def __init__(self) -> None:
        self.upserts: list[tuple[str, tuple[str, ...], tuple[dict, ...]]] = []
        self.commit_count = 0

    def upsert_batch(self, table_schema, rows: tuple[dict, ...], keys: tuple[str, ...]) -> WriteResult:
        self.upserts.append((table_schema.ref.name, keys, rows))
        return WriteResult(success=True, rows_written=len(rows), message="upserted")

    def commit(self) -> None:
        self.commit_count += 1


def test_incremental_config_defaults_disabled() -> None:
    config = load_config(None)

    assert config.incremental.enabled is False


def test_incremental_config_loads_watermarks() -> None:
    config = load_config(Path("tests/fixtures/incremental_config.yml"))

    assert config.incremental.enabled is True
    assert config.incremental.watermarks["users"].column == "created_at"


def test_incremental_migration_upserts_configured_tables_and_skips_missing_watermark() -> None:
    snapshot = load_schema_snapshot_from_json(Path("tests/fixtures/schema_snapshot.json"))
    source = FakeIncrementalSource(
        {
            "users": [
                {"id": 1, "email": "a@example.com", "profile": {}, "created_at": "2026-01-01"},
                {"id": 2, "email": "b@example.com", "profile": {}, "created_at": "2026-01-02"},
            ]
        }
    )
    target = FakeIncrementalTarget()

    report = migrate_incremental_tables(
        job_id="job-1",
        tables=snapshot.tables,
        source=source,
        target=target,
        migration_config=MigrationConfig(batch_size=1, commit_interval=1),
        incremental_config=IncrementalConfig(
            enabled=True,
            watermarks={"users": WatermarkConfig(column="created_at", start_value="2026-01-01")},
        ),
    )

    assert report.rows_upserted == 2
    assert report.tables[0].watermark_start_value == "2026-01-01"
    assert report.tables[0].watermark_end_value is None
    assert report.tables[0].upsert_keys == ("id",)
    assert target.upserts[0][1] == ("id",)
    assert [table.status for table in report.tables] == ["completed", "skipped"]
    assert report.delete_sync_supported is False


def test_incremental_migration_commits_by_uncommitted_rows() -> None:
    snapshot = load_schema_snapshot_from_json(Path("tests/fixtures/schema_snapshot.json"))
    source = FakeIncrementalSource(
        {
            "users": [
                {"id": 1, "email": "a@example.com", "profile": {}, "created_at": "2026-01-01"},
                {"id": 2, "email": "b@example.com", "profile": {}, "created_at": "2026-01-02"},
                {"id": 3, "email": "c@example.com", "profile": {}, "created_at": "2026-01-03"},
                {"id": 4, "email": "d@example.com", "profile": {}, "created_at": "2026-01-04"},
                {"id": 5, "email": "e@example.com", "profile": {}, "created_at": "2026-01-05"},
            ]
        }
    )
    target = FakeIncrementalTarget()

    report = migrate_incremental_tables(
        job_id="job-1",
        tables=(snapshot.tables[0],),
        source=source,
        target=target,
        migration_config=MigrationConfig(batch_size=1, commit_interval=2),
        incremental_config=IncrementalConfig(
            enabled=True,
            watermarks={"users": WatermarkConfig(column="created_at", start_value="2026-01-01")},
        ),
    )

    assert report.rows_upserted == 5
    assert target.commit_count == 3


def test_incremental_migration_skips_table_without_upsert_key() -> None:
    snapshot = load_schema_snapshot_from_json(Path("tests/fixtures/schema_snapshot.json"))
    users = next(table for table in snapshot.tables if table.ref.name == "users")
    no_key_users = type(users)(
        ref=users.ref,
        columns=users.columns,
        primary_key=None,
        indexes=(),
        foreign_keys=users.foreign_keys,
        estimated_rows=users.estimated_rows,
    )

    report = migrate_incremental_tables(
        job_id="job-1",
        tables=(no_key_users,),
        source=FakeIncrementalSource({"users": []}),
        target=FakeIncrementalTarget(),
        migration_config=MigrationConfig(),
        incremental_config=IncrementalConfig(
            enabled=True,
            watermarks={"users": WatermarkConfig(column="created_at", start_value="2026-01-01", end_value="2026-02-01")},
        ),
    )

    assert report.tables[0].status == "skipped"
    assert "Upsert requires" in report.tables[0].message


def test_write_incremental_report_outputs_delete_policy(tmp_path: Path) -> None:
    snapshot = load_schema_snapshot_from_json(Path("tests/fixtures/schema_snapshot.json"))
    report = migrate_incremental_tables(
        job_id="job-1",
        tables=(snapshot.tables[0],),
        source=FakeIncrementalSource({"users": []}),
        target=FakeIncrementalTarget(),
        migration_config=MigrationConfig(),
        incremental_config=IncrementalConfig(
            enabled=True,
            watermarks={"users": WatermarkConfig(column="created_at", start_value="2026-01-01", end_value="2026-02-01")},
        ),
    )

    write_incremental_report(report, tmp_path)

    assert (tmp_path / "summary.json").exists()
    assert (tmp_path / "tables.csv").exists()
    assert (tmp_path / "summary.html").exists()
    html = (tmp_path / "summary.html").read_text(encoding="utf-8")
    assert "Jigration 증분 이관 리포트" in html
    assert "증분 이관 결과" in html
    assert "반영 행 수" in html
    assert "대상 테이블 수" in html
    assert "건너뛴 테이블 수" in html
    assert "증분 기준 컬럼" in html
    assert "watermark 범위" in html
    assert "upsert 기준" in html
    assert "2026-01-01 ~ 2026-02-01" in html
    assert "id" in html
    assert "job_id=" not in html
    assert "delete_sync_supported=" not in html
    assert "Jigration Incremental Report" not in html
    assert "자동 삭제 동기화는 지원하지 않습니다." in (tmp_path / "delete_policy.txt").read_text(encoding="utf-8")
    tables_csv = (tmp_path / "tables.csv").read_text(encoding="utf-8")
    assert "watermark_range" in tables_csv
    assert "upsert_keys" in tables_csv
    assert "2026-01-01 ~ 2026-02-01" in tables_csv
