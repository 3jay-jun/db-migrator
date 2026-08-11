from db_migrator.config.models import AppConfig, TableRunConfig
from db_migrator.schema.models import ForeignKeySchema, SchemaSnapshot, TableRef, TableSchema
from db_migrator.schema.table_mapping import TableMappingResolver


def test_table_mapping_resolver_defaults_to_source_table() -> None:
    resolver = TableMappingResolver(AppConfig())

    assert resolver.target_ref_for(TableRef(schema="public", name="users")) == TableRef(schema="public", name="users")


def test_table_mapping_resolver_maps_target_table_and_foreign_keys() -> None:
    config = AppConfig(
        tables={
            "public.users": TableRunConfig(target_table="app_users"),
            "public.orders": TableRunConfig(target_table="app_orders"),
        }
    )
    resolver = TableMappingResolver(config)
    snapshot = SchemaSnapshot(
        tables=(
            TableSchema(ref=TableRef(schema="public", name="users"), columns=()),
            TableSchema(
                ref=TableRef(schema="public", name="orders"),
                columns=(),
                foreign_keys=(
                    ForeignKeySchema(
                        name="orders_user_id_fkey",
                        columns=("user_id",),
                        referenced_table=TableRef(schema="public", name="users"),
                        referenced_columns=("id",),
                    ),
                ),
            ),
        )
    )

    mapped = resolver.target_snapshot_for(snapshot)

    assert mapped.tables[0].ref == TableRef(schema="public", name="app_users")
    assert mapped.tables[1].ref == TableRef(schema="public", name="app_orders")
    assert mapped.tables[1].foreign_keys[0].referenced_table == TableRef(schema="public", name="app_users")


def test_table_mapping_resolver_uses_default_target_schema() -> None:
    config = AppConfig()
    config.target.schema_name = "app"
    resolver = TableMappingResolver(config)

    assert resolver.target_ref_for(TableRef(schema="public", name="users")) == TableRef(schema="app", name="users")


def test_table_mapping_resolver_allows_table_schema_override() -> None:
    config = AppConfig(tables={"public.users": TableRunConfig(target_schema="custom", target_table="app_users")})
    config.target.schema_name = "app"
    resolver = TableMappingResolver(config)

    assert resolver.target_ref_for(TableRef(schema="public", name="users")) == TableRef(schema="custom", name="app_users")
