from __future__ import annotations

from dataclasses import dataclass

from db_migrator.adapters.providers import DbmsCapabilityError, DbmsProvider, MariaDbProvider, MySqlProvider, PostgresProvider
from db_migrator.config.models import Dbms
from db_migrator.schema.common_types import CommonType


class TypeMappingRegistryError(ValueError):
    pass


@dataclass(frozen=True)
class TypeMappingRegistry:
    providers: dict[Dbms, DbmsProvider]

    def source_type_to_common(self, dbms: Dbms, source_type: str, *, is_generated: bool = False) -> CommonType:
        try:
            provider = self.providers[dbms]
        except KeyError as exc:
            raise TypeMappingRegistryError(f"Unsupported source type DBMS: {dbms.value}") from exc

        try:
            return provider.source_type_to_common(source_type, is_generated=is_generated)
        except DbmsCapabilityError as exc:
            raise TypeMappingRegistryError(str(exc)) from exc

    def common_type_to_target(self, dbms: Dbms, common_type: CommonType) -> str:
        try:
            provider = self.providers[dbms]
        except KeyError as exc:
            raise TypeMappingRegistryError(f"Unsupported target type DBMS: {dbms.value}") from exc
        try:
            return provider.common_type_to_target(common_type)
        except DbmsCapabilityError as exc:
            raise TypeMappingRegistryError(str(exc)) from exc


def default_type_mapping_registry() -> TypeMappingRegistry:
    return TypeMappingRegistry(
        providers={
            Dbms.POSTGRESQL: PostgresProvider(),
            Dbms.MYSQL: MySqlProvider(),
            Dbms.MARIADB: MariaDbProvider(),
        },
    )
