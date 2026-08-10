from db_migrator.schema.common_types import CommonTypeKind, TypePolicy
from db_migrator.schema.type_mapping import common_type_to_mysql, common_type_to_postgres, mysql_type_to_common, postgres_type_to_common


def test_postgres_varchar_maps_to_common_string_and_mysql_varchar() -> None:
    common_type = postgres_type_to_common("character varying(100)")

    assert common_type.kind is CommonTypeKind.STRING
    assert common_type.policy is TypePolicy.AUTO_CONVERT
    assert common_type.length == 100
    assert common_type_to_mysql(common_type) == "varchar(100)"


def test_postgres_jsonb_maps_with_warning() -> None:
    common_type = postgres_type_to_common("jsonb")

    assert common_type.kind is CommonTypeKind.JSON
    assert common_type.policy is TypePolicy.WARN_CONVERT
    assert common_type_to_mysql(common_type) == "json"
    assert common_type.warnings[0].code == "jsonb_semantic_warning"


def test_postgres_timestamp_precision_maps_to_mysql_datetime_precision() -> None:
    common_type = postgres_type_to_common("timestamp without time zone(6)")

    assert common_type.kind is CommonTypeKind.DATETIME
    assert common_type.policy is TypePolicy.AUTO_CONVERT
    assert common_type.precision == 6
    assert common_type_to_mysql(common_type) == "datetime(6)"


def test_postgres_timestamptz_precision_preserves_fractional_seconds_with_warning() -> None:
    common_type = postgres_type_to_common("timestamp with time zone(6)")

    assert common_type.kind is CommonTypeKind.DATETIME
    assert common_type.policy is TypePolicy.WARN_CONVERT
    assert common_type.precision == 6
    assert common_type_to_mysql(common_type) == "datetime(6)"
    assert common_type.warnings[0].code == "timestamp_timezone_warning"


def test_postgres_time_precision_maps_to_mysql_time_precision() -> None:
    common_type = postgres_type_to_common("time without time zone(6)")

    assert common_type.kind is CommonTypeKind.TIME
    assert common_type.policy is TypePolicy.AUTO_CONVERT
    assert common_type.precision == 6
    assert common_type_to_mysql(common_type) == "time(6)"


def test_generated_column_requires_manual_review() -> None:
    common_type = postgres_type_to_common("integer", is_generated=True)

    assert common_type.policy is TypePolicy.MANUAL_REVIEW
    assert common_type.requires_manual_review is True
    assert common_type.warnings[-1].code == "generated_column_manual_review"


def test_mysql_varchar_maps_to_common_string_and_postgres_varchar() -> None:
    common_type = mysql_type_to_common("varchar(100)")

    assert common_type.kind is CommonTypeKind.STRING
    assert common_type.policy is TypePolicy.AUTO_CONVERT
    assert common_type.length == 100
    assert common_type_to_postgres(common_type) == "varchar(100)"


def test_mysql_unsigned_integer_maps_with_warning() -> None:
    common_type = mysql_type_to_common("int unsigned")

    assert common_type.kind is CommonTypeKind.BIGINT
    assert common_type.policy is TypePolicy.WARN_CONVERT
    assert common_type_to_postgres(common_type) == "bigint"
    assert common_type.warnings[0].code == "mysql_unsigned_warning"


def test_mysql_json_maps_to_postgres_jsonb() -> None:
    common_type = mysql_type_to_common("json")

    assert common_type.kind is CommonTypeKind.JSON
    assert common_type.policy is TypePolicy.AUTO_CONVERT
    assert common_type_to_postgres(common_type) == "jsonb"
