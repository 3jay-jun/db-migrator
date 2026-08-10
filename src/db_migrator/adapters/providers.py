from __future__ import annotations

from dataclasses import dataclass

from db_migrator.adapters.base import DdlGenerator, SourceAdapter, TargetAdapter
from db_migrator.adapters.mysql import MySqlDdlGenerator, MySqlSourceAdapter, MySqlTargetAdapter
from db_migrator.adapters.postgres import PostgresDdlGenerator, PostgresSourceAdapter, PostgresTargetAdapter
from db_migrator.config.models import Dbms, SourceConfig, TargetConfig
from db_migrator.schema.common_types import CommonType
from db_migrator.schema.type_mapping import common_type_to_mysql, common_type_to_postgres, mysql_type_to_common, postgres_type_to_common


class DbmsCapabilityError(ValueError):
    pass


class DbmsProvider:
    dbms: Dbms

    def create_source(self, config: SourceConfig) -> SourceAdapter:
        raise DbmsCapabilityError(f"Unsupported source DBMS: {self.dbms.value}")

    def create_target(self, config: TargetConfig) -> TargetAdapter:
        raise DbmsCapabilityError(f"Unsupported target DBMS: {self.dbms.value}")

    def create_ddl_generator(self, *, target_database: str | None = None) -> DdlGenerator:
        raise DbmsCapabilityError(f"Unsupported target DDL DBMS: {self.dbms.value}")

    def source_type_to_common(self, source_type: str, *, is_generated: bool = False) -> CommonType:
        raise DbmsCapabilityError(f"Unsupported source type DBMS: {self.dbms.value}")

    def common_type_to_target(self, common_type: CommonType) -> str:
        raise DbmsCapabilityError(f"Unsupported target type DBMS: {self.dbms.value}")


@dataclass(frozen=True)
class PostgresProvider(DbmsProvider):
    dbms: Dbms = Dbms.POSTGRESQL

    def create_source(self, config: SourceConfig) -> SourceAdapter:
        return PostgresSourceAdapter(config, source_type_mapper=self.source_type_to_common)

    def create_target(self, config: TargetConfig) -> TargetAdapter:
        return PostgresTargetAdapter(config)

    def create_ddl_generator(self, *, target_database: str | None = None) -> DdlGenerator:
        return PostgresDdlGenerator(target_database=target_database, target_type_mapper=self.common_type_to_target)

    def source_type_to_common(self, source_type: str, *, is_generated: bool = False) -> CommonType:
        return postgres_type_to_common(source_type, is_generated=is_generated)

    def common_type_to_target(self, common_type: CommonType) -> str:
        return common_type_to_postgres(common_type)


@dataclass(frozen=True)
class MySqlProvider(DbmsProvider):
    dbms: Dbms = Dbms.MYSQL

    def create_source(self, config: SourceConfig) -> SourceAdapter:
        return MySqlSourceAdapter(config, source_type_mapper=self.source_type_to_common)

    def create_target(self, config: TargetConfig) -> TargetAdapter:
        return MySqlTargetAdapter(config)

    def create_ddl_generator(self, *, target_database: str | None = None) -> DdlGenerator:
        return MySqlDdlGenerator(target_database=target_database, target_type_mapper=self.common_type_to_target)

    def common_type_to_target(self, common_type: CommonType) -> str:
        return common_type_to_mysql(common_type)

    def source_type_to_common(self, source_type: str, *, is_generated: bool = False) -> CommonType:
        return mysql_type_to_common(source_type, is_generated=is_generated)


@dataclass(frozen=True)
class MariaDbProvider(MySqlProvider):
    dbms: Dbms = Dbms.MARIADB
