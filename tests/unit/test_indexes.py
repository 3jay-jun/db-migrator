from db_migrator.config.models import AppConfig, Dbms, IncrementalConfig, MigrationConfig, MigrationMode
from db_migrator.core.indexes import plan_index_migration
from db_migrator.schema.common_types import CommonType, CommonTypeKind, TypePolicy
from db_migrator.schema.models import ColumnSchema, IndexSchema, PrimaryKey, SchemaSnapshot, TableRef, TableSchema


def test_index_policy_defaults_simple_index_to_post_data() -> None:
    table = _table(indexes=(IndexSchema(name="idx_users_email", columns=("email",)),))

    decisions = plan_index_migration(SchemaSnapshot(tables=(table,)), config=AppConfig(), target_dbms=Dbms.MYSQL)

    assert decisions[0].timing == "post_data"
    assert decisions[0].auto_convertible is True
    assert "CREATE INDEX" in (decisions[0].ddl or "")
    assert "`target`.`users`" in (decisions[0].ddl or "")


def test_index_policy_uses_pre_data_for_incremental_unique_key_without_primary_key() -> None:
    table = _table(primary_key=None, indexes=(IndexSchema(name="idx_users_email", columns=("email",), unique=True),))
    config = AppConfig(
        migration=MigrationConfig(mode=MigrationMode.INCREMENTAL),
        incremental=IncrementalConfig(enabled=True),
    )

    decisions = plan_index_migration(SchemaSnapshot(tables=(table,)), config=config, target_dbms=Dbms.MYSQL)

    assert decisions[0].timing == "pre_data"
    assert decisions[0].risk_level == "medium"


def test_index_policy_keeps_complex_index_manual_review() -> None:
    table = _table(
        indexes=(
            IndexSchema(
                name="idx_users_expr",
                columns=(),
                auto_create_candidate=False,
                manual_review_reason="Expression index requires manual conversion.",
            ),
        )
    )

    decisions = plan_index_migration(SchemaSnapshot(tables=(table,)), config=AppConfig(), target_dbms=Dbms.MYSQL)

    assert decisions[0].timing == "manual_review"
    assert decisions[0].auto_convertible is False
    assert decisions[0].ddl is None


def _table(
    *,
    primary_key=PrimaryKey(columns=("id",)),
    indexes: tuple[IndexSchema, ...],
) -> TableSchema:
    return TableSchema(
        ref=TableRef(schema="public", name="users"),
        primary_key=primary_key,
        indexes=indexes,
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
                name="email",
                source_type="text",
                common_type=CommonType(kind=CommonTypeKind.TEXT, policy=TypePolicy.AUTO_CONVERT),
                nullable=True,
                default=None,
                is_generated=False,
                generation_expression=None,
                ordinal_position=2,
            ),
        ),
    )
