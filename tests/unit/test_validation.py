from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from db_migrator.config.models import VerificationConfig
from db_migrator.core.validation import (
    NormalizationProfile,
    ValidationStatus,
    checksum_rows,
    normalize_value,
    validate_tables,
)
from db_migrator.schema.common_types import CommonType, CommonTypeKind, TypePolicy
from db_migrator.schema.models import ColumnSchema, PrimaryKey, TableRef, TableSchema
from db_migrator.schema.snapshot_io import load_schema_snapshot_from_json


class FakeValidationReader:
    def __init__(self, counts: dict[tuple[str, str], int], samples: dict[tuple[str, str], tuple[dict, ...]]) -> None:
        self.counts = counts
        self.samples = samples

    def count_rows(self, side: str, table) -> int:
        return self.counts[(side, table.name)]

    def sample_rows(self, side: str, table_schema, sample_size: int) -> tuple[dict, ...]:
        return self.samples[(side, table_schema.ref.name)][:sample_size]


def test_validate_tables_separates_row_count_and_checksum_mismatch() -> None:
    snapshot = load_schema_snapshot_from_json(Path("tests/fixtures/schema_snapshot.json"))
    users = next(table for table in snapshot.tables if table.ref.name == "users")
    reader = FakeValidationReader(
        counts={
            ("source", "users"): 2,
            ("target", "users"): 1,
        },
        samples={
            ("source", "users"): ({"id": 1, "email": "a@example.com"},),
            ("target", "users"): ({"id": 1, "email": "changed@example.com"},),
        },
    )

    report = validate_tables(
        job_id="job-1",
        tables=(users,),
        reader=reader,
        verification=VerificationConfig(checksum_sample_size=10),
    )

    result = report.tables[0]
    assert result.status == ValidationStatus.MISMATCHED
    assert result.row_count.status == ValidationStatus.MISMATCHED
    assert result.checksum.status == ValidationStatus.MISMATCHED
    assert result.checksum.differences[0].row_number == 1
    assert result.checksum.differences[0].row_identity == "id=1"
    assert result.checksum.differences[0].column == "email"
    assert result.checksum.differences[0].source_value == "a@example.com"
    assert result.checksum.differences[0].target_value == "changed@example.com"


def test_validate_tables_matches_equal_counts_and_samples() -> None:
    snapshot = load_schema_snapshot_from_json(Path("tests/fixtures/schema_snapshot.json"))
    orders = next(table for table in snapshot.tables if table.ref.name == "orders")
    samples = ({"id": 1, "total_amount": Decimal("10.00")},)
    reader = FakeValidationReader(
        counts={
            ("source", "orders"): 1,
            ("target", "orders"): 1,
        },
        samples={
            ("source", "orders"): samples,
            ("target", "orders"): samples,
        },
    )

    report = validate_tables(job_id="job-1", tables=(orders,), reader=reader, verification=VerificationConfig())

    assert report.status == ValidationStatus.MATCHED
    assert report.tables[0].checksum.matched_samples[0].row_identity == "id=1"
    assert report.tables[0].checksum.matched_samples[0].source_values["total_amount"] == "10.00"
    assert report.tables[0].checksum.matched_samples[0].target_values["total_amount"] == "10.00"


def test_validate_tables_normalizes_boolean_tinyint_values() -> None:
    table = TableSchema(
        ref=TableRef(schema="public", name="feature_flags"),
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
            ColumnSchema(
                name="enabled",
                source_type="boolean",
                common_type=CommonType(kind=CommonTypeKind.BOOLEAN, policy=TypePolicy.AUTO_CONVERT),
                nullable=False,
                default=None,
                is_generated=False,
                generation_expression=None,
                ordinal_position=2,
            ),
        ),
        primary_key=PrimaryKey(columns=("id",)),
    )
    reader = FakeValidationReader(
        counts={
            ("source", "feature_flags"): 2,
            ("target", "feature_flags"): 2,
        },
        samples={
            ("source", "feature_flags"): ({"id": 1, "enabled": False}, {"id": 2, "enabled": True}),
            ("target", "feature_flags"): ({"id": 1, "enabled": 0}, {"id": 2, "enabled": 1}),
        },
    )

    report = validate_tables(job_id="job-1", tables=(table,), reader=reader, verification=VerificationConfig())

    result = report.tables[0]
    assert result.status == ValidationStatus.MATCHED
    assert result.checksum.differences == ()
    assert [sample.row_identity for sample in result.checksum.matched_samples] == ["id=1", "id=2"]


def test_validate_tables_matches_primary_key_rows_even_when_sample_order_differs() -> None:
    snapshot = load_schema_snapshot_from_json(Path("tests/fixtures/schema_snapshot.json"))
    users = next(table for table in snapshot.tables if table.ref.name == "users")
    reader = FakeValidationReader(
        counts={
            ("source", "users"): 2,
            ("target", "users"): 2,
        },
        samples={
            ("source", "users"): (
                {"id": 2, "email": "b@example.com"},
                {"id": 1, "email": "a@example.com"},
            ),
            ("target", "users"): (
                {"id": 1, "email": "a@example.com"},
                {"id": 2, "email": "b@example.com"},
            ),
        },
    )

    report = validate_tables(job_id="job-1", tables=(users,), reader=reader, verification=VerificationConfig())

    result = report.tables[0]
    assert result.status == ValidationStatus.MATCHED
    assert result.checksum.differences == ()


def test_validate_tables_normalizes_aware_datetime_to_configured_timezone() -> None:
    snapshot = load_schema_snapshot_from_json(Path("tests/fixtures/schema_snapshot.json"))
    users = next(table for table in snapshot.tables if table.ref.name == "users")
    reader = FakeValidationReader(
        counts={
            ("source", "users"): 1,
            ("target", "users"): 1,
        },
        samples={
            ("source", "users"): (
                {"id": 1, "created_at": datetime(2026, 7, 1, 0, 57, 3, 756512, tzinfo=timezone.utc)},
            ),
            ("target", "users"): (
                {"id": 1, "created_at": datetime(2026, 7, 1, 9, 57, 3, 756512)},
            ),
        },
    )

    report = validate_tables(
        job_id="job-1",
        tables=(users,),
        reader=reader,
        verification=VerificationConfig(checksum_timezone="Asia/Seoul"),
    )

    result = report.tables[0]
    assert result.status == ValidationStatus.MATCHED
    assert result.checksum.differences == ()
    assert result.checksum.matched_samples[0].source_values["created_at"] == "2026-07-01T09:57:03.756512"
    assert result.checksum.matched_samples[0].target_values["created_at"] == "2026-07-01T09:57:03.756512"


def test_validate_tables_reports_primary_key_missing_rows() -> None:
    snapshot = load_schema_snapshot_from_json(Path("tests/fixtures/schema_snapshot.json"))
    users = next(table for table in snapshot.tables if table.ref.name == "users")
    reader = FakeValidationReader(
        counts={
            ("source", "users"): 2,
            ("target", "users"): 2,
        },
        samples={
            ("source", "users"): (
                {"id": 1, "email": "a@example.com"},
                {"id": 2, "email": "b@example.com"},
            ),
            ("target", "users"): (
                {"id": 1, "email": "a@example.com"},
                {"id": 3, "email": "c@example.com"},
            ),
        },
    )

    report = validate_tables(job_id="job-1", tables=(users,), reader=reader, verification=VerificationConfig())

    result = report.tables[0]
    assert result.status == ValidationStatus.MISMATCHED
    assert result.checksum.differences[0].row_identity == "id=2"
    assert result.checksum.differences[0].source_value == "<PRESENT_ROW>"
    assert result.checksum.differences[0].target_value == "<MISSING_ROW>"


def test_checksum_normalization_handles_json_datetime_bytes_and_null() -> None:
    profile = NormalizationProfile(datetime_precision="seconds", float_precision=12)
    rows = (
        {
            "id": 1,
            "payload": {"b": 2, "a": 1},
            "created_at": datetime(2026, 1, 1, 1, 2, 3, 999, tzinfo=timezone.utc),
            "blob": b"abc",
            "empty": None,
        },
    )

    checksum = checksum_rows(rows, profile)

    assert len(checksum) == 64
    assert normalize_value(None, profile) == "<NULL>"
