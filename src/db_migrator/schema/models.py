from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from db_migrator.schema.common_types import CommonType, SchemaWarning


@dataclass(frozen=True)
class TableRef:
    schema: str
    name: str


@dataclass(frozen=True)
class PrimaryKey:
    columns: tuple[str, ...]


@dataclass(frozen=True)
class IndexSchema:
    name: str
    columns: tuple[str, ...]
    unique: bool = False


@dataclass(frozen=True)
class ForeignKeySchema:
    name: str
    columns: tuple[str, ...]
    referenced_table: TableRef
    referenced_columns: tuple[str, ...]


@dataclass(frozen=True)
class ColumnSchema:
    name: str
    source_type: str
    common_type: CommonType
    nullable: bool
    default: str | None
    is_generated: bool
    generation_expression: str | None
    ordinal_position: int
    warnings: tuple[SchemaWarning, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class TableSchema:
    ref: TableRef
    columns: tuple[ColumnSchema, ...]
    primary_key: PrimaryKey | None = None
    indexes: tuple[IndexSchema, ...] = field(default_factory=tuple)
    foreign_keys: tuple[ForeignKeySchema, ...] = field(default_factory=tuple)
    estimated_rows: int | None = None


@dataclass(frozen=True)
class SchemaSnapshot:
    tables: tuple[TableSchema, ...]


RowData = dict[str, Any]


@dataclass(frozen=True)
class ReadCursor:
    offset: int = 0


@dataclass(frozen=True)
class RowBatch:
    table: TableRef
    rows: tuple[RowData, ...]
    batch_number: int
    start_offset: int
    next_cursor: ReadCursor | None

    @property
    def row_count(self) -> int:
        return len(self.rows)


@dataclass(frozen=True)
class WriteResult:
    success: bool
    rows_written: int
    message: str
