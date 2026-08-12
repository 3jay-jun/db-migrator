from db_migrator.config.models import AppConfig, MigrationConfig, SourceOnlyColumnAction, TableRunConfig
from db_migrator.schema.column_plan import build_column_plan
from db_migrator.schema.common_types import CommonType, CommonTypeKind, TypePolicy
from db_migrator.schema.models import ColumnSchema, PrimaryKey, SchemaSnapshot, TableRef, TableSchema
from db_migrator.schema.schema_pair import SchemaOrigin, SchemaPairResolver


def test_schema_pair_uses_existing_mapped_target_schema_first() -> None:
    config = AppConfig(tables={"public.users": TableRunConfig(target_table="app_users")})
    source_table = _table("public", "users", ("id", "email", "legacy_code"))
    target_table = _table("target", "app_users", ("id", "email"))

    plan = SchemaPairResolver(config).resolve(
        source_snapshot=SchemaSnapshot(tables=(source_table,)),
        target_snapshot=SchemaSnapshot(tables=(target_table,)),
    )

    assert plan.pairs[0].schema_origin is SchemaOrigin.TARGET_EXISTING
    assert plan.pairs[0].target_table is target_table
    assert plan.pairs[0].column_plan.write_columns == ("id", "email", "legacy_code")
    assert plan.pairs[0].column_plan.source_only_columns == ()
    assert plan.pairs[0].column_plan.add_column_ddls == (
        "ALTER TABLE `target`.`app_users` ADD COLUMN `legacy_code` longtext NOT NULL;",
    )


def test_schema_pair_falls_back_to_source_mapped_schema_when_target_missing() -> None:
    config = AppConfig(tables={"public.users": TableRunConfig(target_table="app_users")})
    source_table = _table("public", "users", ("id", "email"))

    plan = SchemaPairResolver(config).resolve(
        source_snapshot=SchemaSnapshot(tables=(source_table,)),
        target_snapshot=SchemaSnapshot(tables=()),
    )

    assert plan.pairs[0].schema_origin is SchemaOrigin.SOURCE_MAPPED
    assert plan.pairs[0].target_table.ref == TableRef(schema="target", name="app_users")
    assert plan.pairs[0].column_plan.write_columns == ("id", "email")


def test_column_plan_applies_source_rename_default_null_skip_and_source_only_policy() -> None:
    config = AppConfig(
        tables={
            "public.users": TableRunConfig.model_validate(
                {
                    "columns": {
                        "email_address": {"source": "email"},
                        "status": {"default": "active"},
                        "optional_note": {"null": True},
                        "ignored_target": {"skip": True},
                    },
                    "source_only_columns": {"legacy_code": "add_to_target"},
                }
            )
        }
    )
    source_table = _table("public", "users", ("id", "email", "legacy_code"))
    target_table = _table("public", "users", ("id", "email_address", "status", "optional_note", "ignored_target"))

    plan = build_column_plan(config=config, source_table=source_table, target_table=target_table)

    assert plan.read_columns == ("id", "email", "legacy_code")
    assert plan.write_columns == ("id", "email_address", "status", "optional_note", "legacy_code")
    assert plan.transform_row({"id": 1, "email": "a@example.com", "legacy_code": "x"}) == {
        "id": 1,
        "email_address": "a@example.com",
        "status": "active",
        "optional_note": None,
        "legacy_code": "x",
    }
    assert plan.source_only_columns == ()
    assert plan.add_column_ddls == ("ALTER TABLE `public`.`users` ADD COLUMN `legacy_code` longtext NOT NULL;",)


def test_column_plan_ignores_source_only_column_when_configured_ignore() -> None:
    config = AppConfig(
        tables={
            "public.users": TableRunConfig.model_validate(
                {
                    "source_only_columns": {"legacy_code": "ignore"},
                }
            )
        }
    )
    source_table = _table("public", "users", ("id", "email", "legacy_code"))
    target_table = _table("public", "users", ("id", "email"))

    plan = build_column_plan(config=config, source_table=source_table, target_table=target_table)

    assert plan.read_columns == ("id", "email")
    assert plan.write_columns == ("id", "email")
    assert plan.transform_row({"id": 1, "email": "a@example.com", "legacy_code": "x"}) == {
        "id": 1,
        "email": "a@example.com",
    }
    assert plan.source_only_columns[0].action is SourceOnlyColumnAction.IGNORE
    assert plan.add_column_ddls == ()


def test_column_plan_maps_source_key_columns_to_target_key_columns() -> None:
    config = AppConfig(
        tables={
            "public.users": TableRunConfig.model_validate(
                {
                    "columns": {
                        "user_id": {"source": "id"},
                    },
                }
            )
        }
    )
    source_table = _table("public", "users", ("id", "email"))
    target_table = _table("public", "users", ("user_id", "email"))

    plan = build_column_plan(config=config, source_table=source_table, target_table=target_table)

    assert plan.target_key_columns_for(("id",)) == ("user_id",)
    assert plan.target_key_columns_for(("missing_id",)) == ()


def test_column_plan_treats_configured_missing_target_column_as_add_column_and_mapping() -> None:
    config = AppConfig(
        tables={
            "public.users": TableRunConfig.model_validate(
                {
                    "columns": {
                        "legacy_id": {"source": "legacy_code"},
                    },
                }
            )
        }
    )
    source_table = _table("public", "users", ("id", "legacy_code"))
    target_table = _table("public", "users", ("id",))

    plan = build_column_plan(config=config, source_table=source_table, target_table=target_table)

    assert plan.read_columns == ("id", "legacy_code")
    assert plan.write_columns == ("id", "legacy_id")
    assert plan.transform_row({"id": 1, "legacy_code": "A-1"}) == {"id": 1, "legacy_id": "A-1"}
    assert plan.source_only_columns == ()
    assert plan.add_column_ddls == ("ALTER TABLE `public`.`users` ADD COLUMN `legacy_id` longtext NOT NULL;",)


def test_column_plan_treats_configured_existing_target_column_as_rename_and_mapping() -> None:
    config = AppConfig(
        tables={
            "public.users": TableRunConfig.model_validate(
                {
                    "columns": {
                        "id_": {"source": "id"},
                    },
                }
            )
        }
    )
    source_table = _table("public", "users", ("id", "email"))
    target_table = _table("public", "users", ("id", "email"))

    plan = build_column_plan(config=config, source_table=source_table, target_table=target_table)

    assert plan.read_columns == ("id", "email")
    assert plan.write_columns == ("id_", "email")
    assert plan.transform_row({"id": 1, "email": "a@example.com"}) == {"id_": 1, "email": "a@example.com"}
    assert plan.target_table.primary_key is not None
    assert plan.target_table.primary_key.columns == ("id_",)
    assert plan.rename_column_ddls == ("ALTER TABLE `public`.`users` RENAME COLUMN `id` TO `id_`;",)
    assert plan.add_column_ddls == ()


def test_column_plan_detects_existing_target_column_type_change() -> None:
    source_table = _typed_table("public", "users", {"email": ("character varying(320)", CommonTypeKind.STRING, 320)})
    target_table = _typed_table("public", "users", {"email": ("varchar(255)", CommonTypeKind.STRING, 255)})

    plan = build_column_plan(config=AppConfig(), source_table=source_table, target_table=target_table)

    assert plan.read_columns == ("email",)
    assert plan.write_columns == ("email",)
    assert plan.type_change_ddls == ("ALTER TABLE `public`.`users` MODIFY COLUMN `email` varchar(320) NOT NULL;",)


def test_column_plan_uses_configured_target_type_override() -> None:
    config = AppConfig(
        tables={
            "public.users": TableRunConfig.model_validate(
                {
                    "columns": {
                        "email": {"target_type": "varchar(500)"},
                    },
                }
            )
        }
    )
    source_table = _typed_table("public", "users", {"email": ("varchar(255)", CommonTypeKind.STRING, 255)})
    target_table = _typed_table("public", "users", {"email": ("varchar(255)", CommonTypeKind.STRING, 255)})

    plan = build_column_plan(config=config, source_table=source_table, target_table=target_table)

    assert plan.read_columns == ("email",)
    assert plan.write_columns == ("email",)
    assert plan.type_change_ddls == ("ALTER TABLE `public`.`users` MODIFY COLUMN `email` varchar(500) NOT NULL;",)


def test_column_plan_rejects_unknown_target_type_override() -> None:
    config = AppConfig(
        tables={
            "public.users": TableRunConfig.model_validate(
                {
                    "columns": {
                        "email": {"target_type": "definitely_not_a_type"},
                    },
                }
            )
        }
    )
    source_table = _typed_table("public", "users", {"email": ("varchar(255)", CommonTypeKind.STRING, 255)})
    target_table = _typed_table("public", "users", {"email": ("varchar(255)", CommonTypeKind.STRING, 255)})

    plan = build_column_plan(config=config, source_table=source_table, target_table=target_table)

    assert plan.blocks_execution is True
    assert plan.type_change_ddls == ()
    assert "Configured target type is not valid" in plan.unresolved_target_columns[0].message


def test_column_plan_blocks_required_target_column_without_mapping() -> None:
    source_table = _table("public", "users", ("id",))
    target_table = _table("public", "users", ("id", "email"))

    plan = build_column_plan(config=AppConfig(migration=MigrationConfig()), source_table=source_table, target_table=target_table)

    assert plan.blocks_execution is True
    assert plan.unresolved_target_columns[0].column.name == "email"


def _table(schema: str, name: str, columns: tuple[str, ...]) -> TableSchema:
    return TableSchema(
        ref=TableRef(schema=schema, name=name),
        primary_key=PrimaryKey(columns=("id",)) if "id" in columns else None,
        columns=tuple(
            ColumnSchema(
                name=column,
                source_type="integer" if column == "id" else "text",
                common_type=CommonType(
                    kind=CommonTypeKind.INTEGER if column == "id" else CommonTypeKind.TEXT,
                    policy=TypePolicy.AUTO_CONVERT,
                ),
                nullable=False,
                default=None,
                is_generated=False,
                generation_expression=None,
                ordinal_position=index,
            )
            for index, column in enumerate(columns, start=1)
        ),
    )


def _typed_table(schema: str, name: str, columns: dict[str, tuple[str, CommonTypeKind, int | None]]) -> TableSchema:
    return TableSchema(
        ref=TableRef(schema=schema, name=name),
        primary_key=None,
        columns=tuple(
            ColumnSchema(
                name=column,
                source_type=source_type,
                common_type=CommonType(kind=kind, length=length, policy=TypePolicy.AUTO_CONVERT),
                nullable=False,
                default=None,
                is_generated=False,
                generation_expression=None,
                ordinal_position=index,
            )
            for index, (column, (source_type, kind, length)) in enumerate(columns.items(), start=1)
        ),
    )
