from __future__ import annotations

from dataclasses import dataclass

from db_migrator.adapters.base import DdlGenerator, SourceAdapter, TargetAdapter
from db_migrator.adapters.providers import DbmsCapabilityError, DbmsProvider, MariaDbProvider, MySqlProvider, PostgresProvider
from db_migrator.config.models import Dbms, SourceConfig, TargetConfig


class AdapterRegistryError(ValueError):
    pass


@dataclass(frozen=True)
class DbmsAdapterRegistry:
    providers: dict[Dbms, DbmsProvider]

    def create_source(self, config: SourceConfig) -> SourceAdapter:
        try:
            provider = self.providers[config.dbms]
        except KeyError as exc:
            raise AdapterRegistryError(f"Unsupported source DBMS: {config.dbms.value}") from exc
        try:
            return provider.create_source(config)
        except DbmsCapabilityError as exc:
            raise AdapterRegistryError(str(exc)) from exc

    def create_target(self, config: TargetConfig) -> TargetAdapter:
        try:
            provider = self.providers[config.dbms]
        except KeyError as exc:
            raise AdapterRegistryError(f"Unsupported target DBMS: {config.dbms.value}") from exc
        try:
            return provider.create_target(config)
        except DbmsCapabilityError as exc:
            raise AdapterRegistryError(str(exc)) from exc

    def create_ddl_generator(self, dbms: Dbms, *, target_database: str | None = None) -> DdlGenerator:
        try:
            provider = self.providers[dbms]
        except KeyError as exc:
            raise AdapterRegistryError(f"Unsupported target DDL DBMS: {dbms.value}") from exc
        try:
            return provider.create_ddl_generator(target_database=target_database)
        except DbmsCapabilityError as exc:
            raise AdapterRegistryError(str(exc)) from exc

    def create_source_ddl_generator(self, dbms: Dbms) -> DdlGenerator:
        try:
            provider = self.providers[dbms]
        except KeyError as exc:
            raise AdapterRegistryError(f"Unsupported source DDL DBMS: {dbms.value}") from exc
        try:
            return provider.create_source_ddl_generator()
        except DbmsCapabilityError as exc:
            raise AdapterRegistryError(str(exc)) from exc


def default_adapter_registry() -> DbmsAdapterRegistry:
    return DbmsAdapterRegistry(
        providers={
            Dbms.POSTGRESQL: PostgresProvider(),
            Dbms.MYSQL: MySqlProvider(),
            Dbms.MARIADB: MariaDbProvider(),
        },
    )
