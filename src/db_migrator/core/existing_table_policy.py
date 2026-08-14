from __future__ import annotations

from dataclasses import dataclass

from db_migrator.config.models import ExistingTablePolicy
from db_migrator.schema.column_plan import ColumnPlan
from db_migrator.schema.dependency import plan_table_creation_order
from db_migrator.schema.models import SchemaSnapshot, TableRef, TableSchema
from db_migrator.schema.schema_pair import ResolvedTablePair, SchemaOrigin, SchemaPairPlan


@dataclass(frozen=True)
class ExistingTableDdlItem:
    source_table: TableSchema | None
    target_table: TableSchema
    target_exists: bool
    action: str
    message: str
    destructive: bool = False


@dataclass(frozen=True)
class ExistingTableExecutionPlan:
    ddl_items: tuple[ExistingTableDdlItem, ...]
    dml_pairs: tuple[ResolvedTablePair, ...]

    @property
    def ddl_snapshot(self) -> SchemaSnapshot:
        return SchemaSnapshot(tables=tuple(item.target_table for item in self.ddl_items))

    @property
    def dml_source_tables(self) -> tuple[TableSchema, ...]:
        return tuple(pair.source_table for pair in self.dml_pairs)

    @property
    def column_plans(self) -> dict[TableRef, ColumnPlan]:
        return {pair.source_table.ref: pair.column_plan for pair in self.dml_pairs}

    @property
    def destructive_table_count(self) -> int:
        return sum(1 for item in self.ddl_items if item.destructive)

    @property
    def overwrite_candidates(self) -> tuple[ExistingTableDdlItem, ...]:
        return tuple(item for item in self.ddl_items if item.action == "overwrite")


def build_existing_table_execution_plan(
    schema_plan: SchemaPairPlan,
    policy: ExistingTablePolicy,
    *,
    include_target_only_sync: bool = False,
) -> ExistingTableExecutionPlan:
    ddl_items: list[ExistingTableDdlItem] = []
    dml_pairs: list[ResolvedTablePair] = []

    for pair in schema_plan.pairs:
        item = _ddl_item_for_pair(pair, policy)
        ddl_items.append(item)
        if _includes_dml(pair, policy):
            dml_pairs.append(pair)

    if policy is ExistingTablePolicy.SYNC and include_target_only_sync:
        ddl_items.extend(_target_only_sync_items(schema_plan))

    return ExistingTableExecutionPlan(ddl_items=tuple(ddl_items), dml_pairs=tuple(dml_pairs))


def build_legacy_existing_table_execution_plan(
    *,
    tables: tuple[TableSchema, ...],
    policy: ExistingTablePolicy,
    target_exists: dict[TableRef, bool],
    column_plans: dict[TableRef, ColumnPlan] | None = None,
) -> ExistingTableExecutionPlan:
    ddl_items: list[ExistingTableDdlItem] = []
    dml_pairs: list[ResolvedTablePair] = []
    for table in tables:
        exists = target_exists.get(table.ref, False)
        pair = ResolvedTablePair(
            source_table=table,
            target_table=table,
            schema_origin=_schema_origin(exists),
            column_plan=(column_plans or {}).get(table.ref) or _empty_column_plan(table),
        )
        ddl_items.append(_ddl_item_for_pair(pair, policy))
        if _includes_dml(pair, policy):
            dml_pairs.append(pair)
    return ExistingTableExecutionPlan(ddl_items=tuple(ddl_items), dml_pairs=tuple(dml_pairs))


def _ddl_item_for_pair(pair: ResolvedTablePair, policy: ExistingTablePolicy) -> ExistingTableDdlItem:
    ddl_target_table = pair.column_plan.target_table
    if not pair.target_exists:
        return ExistingTableDdlItem(
            source_table=pair.source_table,
            target_table=ddl_target_table,
            target_exists=False,
            action="create",
            message="Target table does not exist; CREATE will run.",
        )

    if policy is ExistingTablePolicy.APPEND:
        return ExistingTableDdlItem(
            source_table=pair.source_table,
            target_table=ddl_target_table,
            target_exists=True,
            action="skip",
            message="Target table already exists; append policy migrates only missing target tables.",
        )

    if policy is ExistingTablePolicy.SYNC:
        return ExistingTableDdlItem(
            source_table=pair.source_table,
            target_table=ddl_target_table,
            target_exists=True,
            action="sync_existing",
            message="Target table already exists; CREATE skipped for sync policy.",
            destructive=True,
        )

    if policy is ExistingTablePolicy.TRUNCATE_RELOAD:
        return ExistingTableDdlItem(
            source_table=pair.source_table,
            target_table=ddl_target_table,
            target_exists=True,
            action="truncate",
            message="Target table already exists; TRUNCATE will run before DML.",
            destructive=True,
        )

    if policy is ExistingTablePolicy.OVERWRITE:
        return ExistingTableDdlItem(
            source_table=pair.source_table,
            target_table=ddl_target_table,
            target_exists=True,
            action="overwrite",
            message="Target table already exists; DROP and CREATE will run.",
            destructive=True,
        )

    return ExistingTableDdlItem(
        source_table=pair.source_table,
        target_table=ddl_target_table,
        target_exists=True,
        action="skip",
        message="Target table already exists; CREATE skipped and DML is allowed.",
    )


def _target_only_sync_items(schema_plan: SchemaPairPlan) -> tuple[ExistingTableDdlItem, ...]:
    if not schema_plan.target_only_tables:
        return ()

    drop_refs = {table.ref for table in schema_plan.target_only_tables}
    blockers = _target_only_drop_blockers(schema_plan, drop_refs)
    ordered_tables = _drop_order(schema_plan.target_only_tables)
    items: list[ExistingTableDdlItem] = []
    for table in ordered_tables:
        if table.ref in blockers:
            items.append(
                ExistingTableDdlItem(
                    source_table=None,
                    target_table=table,
                    target_exists=True,
                    action="blocked",
                    message=blockers[table.ref],
                )
            )
            continue
        items.append(
            ExistingTableDdlItem(
                source_table=None,
                target_table=table,
                target_exists=True,
                action="drop_target_only",
                message="Target table does not exist in source scope; DROP will run for sync policy.",
                destructive=True,
            )
        )
    return tuple(items)


def _target_only_drop_blockers(schema_plan: SchemaPairPlan, drop_refs: set[TableRef]) -> dict[TableRef, str]:
    blockers: dict[TableRef, list[str]] = {}
    kept_tables = tuple(pair.target_table for pair in schema_plan.pairs)
    kept_tables += tuple(table for table in schema_plan.target_only_tables if table.ref not in drop_refs)
    for table in kept_tables:
        for foreign_key in table.foreign_keys:
            if foreign_key.referenced_table in drop_refs:
                blockers.setdefault(foreign_key.referenced_table, []).append(
                    f"{table.ref.schema}.{table.ref.name}.{foreign_key.name}"
                )
    return {
        table_ref: "Sync target-only drop blocked because kept target constraints reference this table: "
        + ", ".join(sorted(references))
        for table_ref, references in blockers.items()
    }


def _drop_order(tables: tuple[TableSchema, ...]) -> tuple[TableSchema, ...]:
    table_by_ref = {table.ref: table for table in tables}
    creation_order = plan_table_creation_order(SchemaSnapshot(tables=tables)).creation_order
    ordered = tuple(table_by_ref[table_ref] for table_ref in reversed(creation_order) if table_ref in table_by_ref)
    if len(ordered) == len(tables):
        return ordered
    ordered_refs = {table.ref for table in ordered}
    return ordered + tuple(table for table in tables if table.ref not in ordered_refs)


def _includes_dml(pair: ResolvedTablePair, policy: ExistingTablePolicy) -> bool:
    if policy is ExistingTablePolicy.COMPARE_ONLY:
        return False
    if policy is ExistingTablePolicy.APPEND and pair.target_exists:
        return False
    return True


def _schema_origin(target_exists: bool) -> SchemaOrigin:
    return SchemaOrigin.TARGET_EXISTING if target_exists else SchemaOrigin.SOURCE_MAPPED


def _empty_column_plan(table: TableSchema) -> ColumnPlan:
    return ColumnPlan(
        source_table=table,
        target_table=table,
        read_columns=(),
        write_columns=(),
        source_only_columns=(),
        unresolved_target_columns=(),
        _mappings=(),
    )
