from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from db_migrator.config.models import VerificationConfig
from db_migrator.schema.common_types import CommonTypeKind
from db_migrator.schema.models import RowData, TableRef, TableSchema


class ValidationStatus:
    MATCHED = "matched"
    MISMATCHED = "mismatched"
    SKIPPED = "skipped"
    FAILED = "failed"


@dataclass(frozen=True)
class NormalizationProfile:
    datetime_precision: str
    float_precision: int
    datetime_timezone: str | None = None
    null_token: str = "<NULL>"
    bytes_encoding: str = "base64"
    json_sort_keys: bool = True
    boolean_columns: tuple[str, ...] = ()


@dataclass(frozen=True)
class RowCountValidationResult:
    table: TableRef
    status: str
    source_rows: int | None
    target_rows: int | None
    message: str | None = None


@dataclass(frozen=True)
class SampleValueDifference:
    row_number: int
    row_identity: str
    column: str
    source_value: str
    target_value: str


@dataclass(frozen=True)
class MatchedSampleRow:
    row_identity: str
    source_values: dict[str, str]
    target_values: dict[str, str]


@dataclass(frozen=True)
class ValidationEndpoint:
    dbms: str
    host: str
    port: int
    database: str
    schema: str | None = None


@dataclass(frozen=True)
class ValidationMetadata:
    generated_at: str
    source: ValidationEndpoint | None = None
    target: ValidationEndpoint | None = None
    checksum_sample_size: int | None = None
    checksum_timezone: str | None = None
    checksum_datetime_precision: str | None = None


@dataclass(frozen=True)
class ChecksumValidationResult:
    table: TableRef
    status: str
    source_checksum: str | None
    target_checksum: str | None
    sample_size: int
    normalization_profile: NormalizationProfile
    differences: tuple[SampleValueDifference, ...] = ()
    matched_samples: tuple[MatchedSampleRow, ...] = ()
    message: str | None = None


@dataclass(frozen=True)
class TableValidationResult:
    table: TableRef
    row_count: RowCountValidationResult
    checksum: ChecksumValidationResult

    @property
    def status(self) -> str:
        if self.row_count.status == ValidationStatus.FAILED or self.checksum.status == ValidationStatus.FAILED:
            return ValidationStatus.FAILED
        if self.row_count.status == ValidationStatus.MISMATCHED or self.checksum.status == ValidationStatus.MISMATCHED:
            return ValidationStatus.MISMATCHED
        return ValidationStatus.MATCHED


@dataclass(frozen=True)
class ValidationReport:
    job_id: str
    tables: tuple[TableValidationResult, ...]
    metadata: ValidationMetadata

    @property
    def status(self) -> str:
        if any(table.status == ValidationStatus.FAILED for table in self.tables):
            return ValidationStatus.FAILED
        if any(table.status == ValidationStatus.MISMATCHED for table in self.tables):
            return ValidationStatus.MISMATCHED
        return ValidationStatus.MATCHED


class ValidationReader(Protocol):
    def count_rows(self, side: str, table: TableRef) -> int:
        """Return row count for one side."""

    def sample_rows(self, side: str, table_schema: TableSchema, sample_size: int) -> tuple[RowData, ...]:
        """Return deterministic sample rows for checksum verification."""


def validate_tables(
    *,
    job_id: str,
    tables: tuple[TableSchema, ...],
    reader: ValidationReader,
    verification: VerificationConfig,
    metadata: ValidationMetadata | None = None,
) -> ValidationReport:
    profile = NormalizationProfile(
        datetime_precision=verification.checksum_datetime_precision,
        float_precision=verification.checksum_float_precision,
        datetime_timezone=verification.checksum_timezone,
    )
    return ValidationReport(
        job_id=job_id,
        metadata=metadata or _default_metadata(verification),
        tables=tuple(_validate_one_table(table, reader, verification, profile) for table in tables),
    )


def _default_metadata(verification: VerificationConfig) -> ValidationMetadata:
    return ValidationMetadata(
        generated_at=datetime.now(timezone.utc).isoformat(),
        checksum_sample_size=verification.checksum_sample_size,
        checksum_timezone=verification.checksum_timezone,
        checksum_datetime_precision=verification.checksum_datetime_precision,
    )


def _validate_one_table(
    table: TableSchema,
    reader: ValidationReader,
    verification: VerificationConfig,
    profile: NormalizationProfile,
) -> TableValidationResult:
    table_profile = _profile_for_table(table, profile)
    row_count = _validate_row_count(table, reader) if verification.row_count else _skipped_row_count(table)
    checksum = _validate_checksum(table, reader, verification.checksum_sample_size, table_profile) if verification.checksum_sample else _skipped_checksum(table, table_profile)
    return TableValidationResult(table=table.ref, row_count=row_count, checksum=checksum)


def _validate_row_count(table: TableSchema, reader: ValidationReader) -> RowCountValidationResult:
    try:
        source_rows = reader.count_rows("source", table.ref)
        target_rows = reader.count_rows("target", table.ref)
    except Exception as exc:
        return RowCountValidationResult(table=table.ref, status=ValidationStatus.FAILED, source_rows=None, target_rows=None, message=str(exc))

    status = ValidationStatus.MATCHED if source_rows == target_rows else ValidationStatus.MISMATCHED
    return RowCountValidationResult(table=table.ref, status=status, source_rows=source_rows, target_rows=target_rows)


def _validate_checksum(
    table: TableSchema,
    reader: ValidationReader,
    sample_size: int,
    profile: NormalizationProfile,
) -> ChecksumValidationResult:
    try:
        source_rows = reader.sample_rows("source", table, sample_size)
        target_rows = reader.sample_rows("target", table, sample_size)
        source_checksum = checksum_rows(_rows_for_checksum(table, source_rows, profile), profile)
        target_checksum = checksum_rows(_rows_for_checksum(table, target_rows, profile), profile)
    except Exception as exc:
        return ChecksumValidationResult(
            table=table.ref,
            status=ValidationStatus.FAILED,
            source_checksum=None,
            target_checksum=None,
            sample_size=sample_size,
            normalization_profile=profile,
            message=str(exc),
        )

    status = ValidationStatus.MATCHED if source_checksum == target_checksum else ValidationStatus.MISMATCHED
    differences = (
        _sample_value_differences(table, source_rows, target_rows, profile)
        if status == ValidationStatus.MISMATCHED
        else ()
    )
    matched_samples = _matched_sample_rows(table, source_rows, target_rows, profile, limit=3)
    return ChecksumValidationResult(
        table=table.ref,
        status=status,
        source_checksum=source_checksum,
        target_checksum=target_checksum,
        sample_size=sample_size,
        normalization_profile=profile,
        differences=differences,
        matched_samples=matched_samples,
    )


def checksum_rows(rows: tuple[RowData, ...], profile: NormalizationProfile) -> str:
    normalized_rows = [_normalize_row(row, profile) for row in rows]
    payload = "\n".join(normalized_rows).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _rows_for_checksum(table: TableSchema, rows: tuple[RowData, ...], profile: NormalizationProfile) -> tuple[RowData, ...]:
    if not _has_primary_key(table):
        return rows
    return tuple(sorted(rows, key=lambda row: _row_key(table, row, profile)))


def _normalize_row(row: RowData, profile: NormalizationProfile) -> str:
    normalized = {key: _normalize_cell_value(key, value, profile) for key, value in sorted(row.items())}
    return json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def normalize_value(value: Any, profile: NormalizationProfile) -> str:
    return _normalize_scalar_value(value, profile)


def _normalize_cell_value(column: str, value: Any, profile: NormalizationProfile) -> str:
    if column in profile.boolean_columns:
        return _normalize_boolean_value(value, profile)
    return _normalize_scalar_value(value, profile)


def _normalize_scalar_value(value: Any, profile: NormalizationProfile) -> str:
    if value is None:
        return profile.null_token
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, bytes):
        return base64.b64encode(value).decode("ascii")
    if isinstance(value, datetime):
        normalized_datetime = _normalize_datetime(value, profile)
        if profile.datetime_precision == "seconds":
            normalized_datetime = normalized_datetime.replace(microsecond=0)
        return normalized_datetime.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float):
        return f"{value:.{profile.float_precision}g}"
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=profile.json_sort_keys, separators=(",", ":"))
    return str(value)


def _normalize_boolean_value(value: Any, profile: NormalizationProfile) -> str:
    if value is None:
        return profile.null_token
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int) and value in {0, 1}:
        return "true" if value == 1 else "false"
    if isinstance(value, str):
        normalized_value = value.strip().lower()
        if normalized_value in {"true", "t", "1", "yes", "y"}:
            return "true"
        if normalized_value in {"false", "f", "0", "no", "n"}:
            return "false"
    return _normalize_scalar_value(value, profile)


def _normalize_datetime(value: datetime, profile: NormalizationProfile) -> datetime:
    if value.tzinfo is None:
        return value
    if profile.datetime_timezone is None:
        return value.astimezone(timezone.utc)
    return value.astimezone(ZoneInfo(profile.datetime_timezone)).replace(tzinfo=None)


def _skipped_row_count(table: TableSchema) -> RowCountValidationResult:
    return RowCountValidationResult(table=table.ref, status=ValidationStatus.SKIPPED, source_rows=None, target_rows=None)


def _skipped_checksum(table: TableSchema, profile: NormalizationProfile) -> ChecksumValidationResult:
    return ChecksumValidationResult(
        table=table.ref,
        status=ValidationStatus.SKIPPED,
        source_checksum=None,
        target_checksum=None,
        sample_size=0,
        normalization_profile=profile,
    )


def _sample_value_differences(
    table: TableSchema,
    source_rows: tuple[RowData, ...],
    target_rows: tuple[RowData, ...],
    profile: NormalizationProfile,
    limit: int = 20,
) -> tuple[SampleValueDifference, ...]:
    if _has_primary_key(table):
        return _sample_value_differences_by_key(table, source_rows, target_rows, profile, limit)
    return _sample_value_differences_by_position(table, source_rows, target_rows, profile, limit)


def _matched_sample_rows(
    table: TableSchema,
    source_rows: tuple[RowData, ...],
    target_rows: tuple[RowData, ...],
    profile: NormalizationProfile,
    limit: int,
) -> tuple[MatchedSampleRow, ...]:
    if _has_primary_key(table):
        return _matched_sample_rows_by_key(table, source_rows, target_rows, profile, limit)
    return _matched_sample_rows_by_position(table, source_rows, target_rows, profile, limit)


def _matched_sample_rows_by_key(
    table: TableSchema,
    source_rows: tuple[RowData, ...],
    target_rows: tuple[RowData, ...],
    profile: NormalizationProfile,
    limit: int,
) -> tuple[MatchedSampleRow, ...]:
    matches: list[MatchedSampleRow] = []
    source_by_key = {_row_key(table, row, profile): row for row in source_rows}
    target_by_key = {_row_key(table, row, profile): row for row in target_rows}
    for row_key in sorted(set(source_by_key) & set(target_by_key)):
        if len(matches) >= limit:
            break
        matched_row = _matched_sample_row(table, source_by_key[row_key], target_by_key[row_key], profile)
        if matched_row is not None:
            matches.append(matched_row)
    return tuple(matches)


def _matched_sample_rows_by_position(
    table: TableSchema,
    source_rows: tuple[RowData, ...],
    target_rows: tuple[RowData, ...],
    profile: NormalizationProfile,
    limit: int,
) -> tuple[MatchedSampleRow, ...]:
    matches: list[MatchedSampleRow] = []
    for source_row, target_row in zip(source_rows, target_rows, strict=False):
        if len(matches) >= limit:
            break
        matched_row = _matched_sample_row(table, source_row, target_row, profile)
        if matched_row is not None:
            matches.append(matched_row)
    return tuple(matches)


def _matched_sample_row(
    table: TableSchema,
    source_row: RowData,
    target_row: RowData,
    profile: NormalizationProfile,
) -> MatchedSampleRow | None:
    if _row_differences(table, source_row, target_row, profile, row_number=1, limit=1):
        return None
    return MatchedSampleRow(
        row_identity=_row_identity(table, source_row, target_row, profile),
        source_values=_normalized_row_values(source_row, profile),
        target_values=_normalized_row_values(target_row, profile),
    )


def _normalized_row_values(row: RowData, profile: NormalizationProfile) -> dict[str, str]:
    return {
        column: _normalize_cell_value(column, value, profile)
        for column, value in sorted(row.items())
    }


def _sample_value_differences_by_key(
    table: TableSchema,
    source_rows: tuple[RowData, ...],
    target_rows: tuple[RowData, ...],
    profile: NormalizationProfile,
    limit: int,
) -> tuple[SampleValueDifference, ...]:
    differences: list[SampleValueDifference] = []
    source_by_key = {_row_key(table, row, profile): row for row in source_rows}
    target_by_key = {_row_key(table, row, profile): row for row in target_rows}
    for row_index, row_key in enumerate(sorted(set(source_by_key) | set(target_by_key)), start=1):
        if len(differences) >= limit:
            break
        source_row = source_by_key.get(row_key)
        target_row = target_by_key.get(row_key)
        differences.extend(_row_differences(table, source_row, target_row, profile, row_index, limit - len(differences)))
    return tuple(differences)


def _sample_value_differences_by_position(
    table: TableSchema,
    source_rows: tuple[RowData, ...],
    target_rows: tuple[RowData, ...],
    profile: NormalizationProfile,
    limit: int,
) -> tuple[SampleValueDifference, ...]:
    differences: list[SampleValueDifference] = []
    max_len = max(len(source_rows), len(target_rows))
    for row_index in range(max_len):
        if len(differences) >= limit:
            break
        source_row = source_rows[row_index] if row_index < len(source_rows) else None
        target_row = target_rows[row_index] if row_index < len(target_rows) else None
        differences.extend(_row_differences(table, source_row, target_row, profile, row_index + 1, limit - len(differences)))
    return tuple(differences)


def _row_differences(
    table: TableSchema,
    source_row: RowData | None,
    target_row: RowData | None,
    profile: NormalizationProfile,
    row_number: int,
    limit: int,
) -> list[SampleValueDifference]:
    row_identity = _row_identity(table, source_row, target_row, profile)
    if source_row is None or target_row is None:
        return [
            SampleValueDifference(
                row_number=row_number,
                row_identity=row_identity,
                column="<row>",
                source_value=_row_presence(source_row),
                target_value=_row_presence(target_row),
            )
        ]

    differences: list[SampleValueDifference] = []
    columns = sorted(set(source_row) | set(target_row))
    for column in columns:
        if len(differences) >= limit:
            break
        source_value = _normalize_optional_cell(source_row, column, profile)
        target_value = _normalize_optional_cell(target_row, column, profile)
        if source_value == target_value:
            continue
        differences.append(
            SampleValueDifference(
                row_number=row_number,
                row_identity=row_identity,
                column=column,
                source_value=source_value,
                target_value=target_value,
            )
        )
    return differences


def _row_identity(
    table: TableSchema,
    source_row: RowData | None,
    target_row: RowData | None,
    profile: NormalizationProfile,
) -> str:
    row = source_row or target_row or {}
    if table.primary_key is not None and table.primary_key.columns:
        values = [
            f"{column}={_normalize_optional_cell(row, column, profile)}"
            for column in table.primary_key.columns
        ]
        return ", ".join(values)
    if not row:
        return f"sample_row={id(row)}"
    first_column = sorted(row)[0]
    return f"{first_column}={_normalize_optional_cell(row, first_column, profile)}"


def _row_key(table: TableSchema, row: RowData, profile: NormalizationProfile) -> tuple[str, ...]:
    if table.primary_key is None:
        return tuple()
    return tuple(_normalize_optional_cell(row, column, profile) for column in table.primary_key.columns)


def _has_primary_key(table: TableSchema) -> bool:
    return table.primary_key is not None and bool(table.primary_key.columns)


def _normalize_optional_cell(row: RowData, column: str, profile: NormalizationProfile) -> str:
    if column not in row:
        return "<MISSING>"
    return _normalize_cell_value(column, row[column], profile)


def _row_presence(row: RowData | None) -> str:
    return "<MISSING_ROW>" if row is None else "<PRESENT_ROW>"


def _profile_for_table(table: TableSchema, profile: NormalizationProfile) -> NormalizationProfile:
    boolean_columns = tuple(
        column.name
        for column in table.columns
        if column.common_type.kind is CommonTypeKind.BOOLEAN
    )
    return NormalizationProfile(
        datetime_precision=profile.datetime_precision,
        float_precision=profile.float_precision,
        datetime_timezone=profile.datetime_timezone,
        null_token=profile.null_token,
        bytes_encoding=profile.bytes_encoding,
        json_sort_keys=profile.json_sort_keys,
        boolean_columns=boolean_columns,
    )
