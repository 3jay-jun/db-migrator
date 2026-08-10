from __future__ import annotations

from dataclasses import dataclass

from db_migrator.schema.models import ForeignKeySchema, SchemaSnapshot, TableRef, TableSchema


@dataclass(frozen=True)
class DependencyPlan:
    creation_order: tuple[TableRef, ...]
    manual_review: tuple[str, ...]


def plan_table_creation_order(snapshot: SchemaSnapshot) -> DependencyPlan:
    tables = {table.ref: table for table in snapshot.tables}
    dependencies = {
        table.ref: {foreign_key.referenced_table for foreign_key in table.foreign_keys if foreign_key.referenced_table in tables}
        for table in snapshot.tables
    }
    manual_review = [
        f"{table.ref.schema}.{table.ref.name}.{foreign_key.name} references excluded table "
        f"{foreign_key.referenced_table.schema}.{foreign_key.referenced_table.name}"
        for table in snapshot.tables
        for foreign_key in table.foreign_keys
        if foreign_key.referenced_table not in tables
    ]

    creation_order: list[TableRef] = []
    remaining = {table.ref for table in snapshot.tables}
    while remaining:
        ready = sorted(
            [table for table in remaining if not dependencies[table] - set(creation_order)],
            key=lambda table: (table.schema, table.name),
        )
        if not ready:
            cycle_tables = ", ".join(f"{table.schema}.{table.name}" for table in sorted(remaining, key=lambda ref: (ref.schema, ref.name)))
            manual_review.append(f"Cycle detected in FK dependency graph: {cycle_tables}")
            creation_order.extend(sorted(remaining, key=lambda table: (table.schema, table.name)))
            break
        creation_order.extend(ready)
        remaining -= set(ready)

    return DependencyPlan(creation_order=tuple(creation_order), manual_review=tuple(manual_review))


def foreign_keys_for_table(table: TableSchema) -> tuple[ForeignKeySchema, ...]:
    return table.foreign_keys
