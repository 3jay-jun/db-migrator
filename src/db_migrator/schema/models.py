from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
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
    method: str | None = None
    auto_create_candidate: bool = True
    manual_review_reason: str | None = None


class SchemaObjectKind(StrEnum):
    VIEW = "view"
    FUNCTION = "function"
    PROCEDURE = "procedure"
    TRIGGER = "trigger"


@dataclass(frozen=True)
class SchemaObjectSummary:
    kind: SchemaObjectKind
    schema: str
    name: str
    parent_table: TableRef | None = None


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
    auto_increment: bool = False
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
    non_table_objects: tuple[SchemaObjectSummary, ...] = field(default_factory=tuple)


RowData = dict[str, Any]


class CursorStrategy(StrEnum):
    OFFSET = "offset"
    KEYSET = "keyset"


class SamplePosition(StrEnum):
    FIRST = "first"
    LAST = "last"


@dataclass(frozen=True)
class ReadCursor:
    strategy: CursorStrategy = CursorStrategy.OFFSET
    offset: int = 0
    key_columns: tuple[str, ...] = ()
    last_key_values: tuple[Any, ...] = ()

    @classmethod
    def offset_cursor(cls, offset: int = 0) -> "ReadCursor":
        return cls(strategy=CursorStrategy.OFFSET, offset=offset)

    @classmethod
    def keyset_cursor(
        cls,
        *,
        key_columns: tuple[str, ...],
        last_key_values: tuple[Any, ...] = (),
        offset: int = 0,
    ) -> "ReadCursor":
        return cls(
            strategy=CursorStrategy.KEYSET,
            offset=offset,
            key_columns=key_columns,
            last_key_values=last_key_values,
        )


@dataclass(frozen=True)
class RowBatch:
    table: TableRef
    rows: tuple[RowData, ...]
    batch_number: int
    start_offset: int
    next_cursor: ReadCursor | None
    start_cursor: ReadCursor | None = None

    @property
    def row_count(self) -> int:
        return len(self.rows)

    @property
    def failure_cursor(self) -> ReadCursor:
        if self.start_cursor is not None:
            return self.start_cursor
        return ReadCursor.offset_cursor(self.start_offset)


@dataclass(frozen=True)
class WriteResult:
    success: bool
    rows_written: int
    message: str
    rows_inserted: int = 0
    rows_updated: int = 0
    rows_unchanged: int = 0
