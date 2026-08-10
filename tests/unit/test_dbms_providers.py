from db_migrator.adapters.mysql import MySqlDdlGenerator, MySqlSourceAdapter, MySqlTargetAdapter
from db_migrator.adapters.postgres import PostgresDdlGenerator, PostgresSourceAdapter, PostgresTargetAdapter
from db_migrator.adapters.providers import MariaDbProvider, MySqlProvider, PostgresProvider
from db_migrator.config.models import Dbms, SourceConfig, TargetConfig
from db_migrator.schema.common_types import CommonTypeKind


def test_postgres_provider_exposes_source_and_target_capabilities() -> None:
    provider = PostgresProvider()

    source = provider.create_source(SourceConfig(dbms=Dbms.POSTGRESQL))
    target = provider.create_target(TargetConfig(dbms=Dbms.POSTGRESQL))
    ddl_generator = provider.create_ddl_generator()
    common_type = provider.source_type_to_common("integer")

    assert isinstance(source, PostgresSourceAdapter)
    assert isinstance(target, PostgresTargetAdapter)
    assert isinstance(ddl_generator, PostgresDdlGenerator)
    assert common_type.kind is CommonTypeKind.INTEGER


def test_mysql_provider_exposes_source_and_target_capabilities() -> None:
    provider = MySqlProvider()

    source = provider.create_source(SourceConfig(dbms=Dbms.MYSQL))
    target = provider.create_target(TargetConfig(dbms=Dbms.MYSQL))
    ddl_generator = provider.create_ddl_generator(target_database="target_db")
    common_type = provider.source_type_to_common("varchar(100)")

    assert isinstance(source, MySqlSourceAdapter)
    assert isinstance(target, MySqlTargetAdapter)
    assert isinstance(ddl_generator, MySqlDdlGenerator)
    assert common_type.kind is CommonTypeKind.STRING


def test_mariadb_provider_reuses_mysql_target_capabilities() -> None:
    provider = MariaDbProvider()

    target = provider.create_target(TargetConfig(dbms=Dbms.MARIADB))

    assert isinstance(target, MySqlTargetAdapter)
    assert provider.dbms is Dbms.MARIADB
