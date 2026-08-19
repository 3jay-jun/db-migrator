from __future__ import annotations

from db_migrator.schema.models import TableSchema


def writable_columns(table: TableSchema) -> tuple[str, ...]:
    return tuple(column.name for column in table.columns if not column.is_generated)


def stable_order_columns(table: TableSchema, columns: tuple[str, ...]) -> tuple[str, ...]:
    if table.primary_key is not None and table.primary_key.columns:
        return tuple(column for column in table.primary_key.columns if column in columns)
    return columns


def key_columns_for_resume(table: TableSchema, order_by: tuple[str, ...]) -> tuple[str, ...]:
    if table.primary_key is not None and table.primary_key.columns:
        return tuple(column for column in table.primary_key.columns if column in order_by)
    for index in table.indexes:
        if index.unique:
            return tuple(column for column in index.columns if column in order_by)
    return ()


def key_columns_for_upsert(table: TableSchema) -> tuple[str, ...]:
    if table.primary_key is not None and table.primary_key.columns:
        return table.primary_key.columns
    for index in table.indexes:
        if index.unique:
            return index.columns
    return ()
