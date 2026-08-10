from db_migrator.config.models import Dbms
from db_migrator.schema.common_types import CommonTypeKind
from db_migrator.schema.type_registry import default_type_mapping_registry


def test_default_type_registry_maps_supported_direction() -> None:
    registry = default_type_mapping_registry()

    common_type = registry.source_type_to_common(Dbms.POSTGRESQL, "character varying(100)")

    assert common_type.kind is CommonTypeKind.STRING
    assert registry.common_type_to_target(Dbms.MYSQL, common_type) == "varchar(100)"


def test_default_type_registry_maps_mysql_source_type_to_common() -> None:
    registry = default_type_mapping_registry()

    common_type = registry.source_type_to_common(Dbms.MYSQL, "varchar(100)")

    assert common_type.kind is CommonTypeKind.STRING
    assert common_type.length == 100


def test_default_type_registry_maps_common_type_to_postgres_target_type() -> None:
    registry = default_type_mapping_registry()
    common_type = registry.source_type_to_common(Dbms.POSTGRESQL, "integer")

    assert registry.common_type_to_target(Dbms.POSTGRESQL, common_type) == "integer"
