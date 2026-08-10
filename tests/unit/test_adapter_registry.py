from db_migrator.adapters.mysql import MySqlDdlGenerator, MySqlSourceAdapter, MySqlTargetAdapter
from db_migrator.adapters.postgres import PostgresDdlGenerator, PostgresSourceAdapter, PostgresTargetAdapter
from db_migrator.adapters.registry import default_adapter_registry
from db_migrator.config.models import Dbms, SourceConfig, TargetConfig


def test_default_registry_creates_supported_postgres_to_mysql_components() -> None:
    registry = default_adapter_registry()

    source = registry.create_source(SourceConfig(dbms=Dbms.POSTGRESQL))
    target = registry.create_target(TargetConfig(dbms=Dbms.MYSQL))
    ddl_generator = registry.create_ddl_generator(Dbms.MYSQL, target_database="target_db")

    assert isinstance(source, PostgresSourceAdapter)
    assert isinstance(target, MySqlTargetAdapter)
    assert isinstance(ddl_generator, MySqlDdlGenerator)


def test_default_registry_creates_supported_mysql_to_postgres_components() -> None:
    registry = default_adapter_registry()

    source = registry.create_source(SourceConfig(dbms=Dbms.MYSQL))
    target = registry.create_target(TargetConfig(dbms=Dbms.POSTGRESQL))
    ddl_generator = registry.create_ddl_generator(Dbms.POSTGRESQL, target_database="target_db")

    assert isinstance(source, MySqlSourceAdapter)
    assert isinstance(target, PostgresTargetAdapter)
    assert isinstance(ddl_generator, PostgresDdlGenerator)
