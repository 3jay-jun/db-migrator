from __future__ import annotations

import re

from db_migrator.schema.common_types import (
    CommonType,
    CommonTypeKind,
    SchemaWarning,
    TypePolicy,
)


_VARCHAR_PATTERN = re.compile(r"^(?:character varying|varchar)\((?P<length>\d+)\)$")
_CHAR_PATTERN = re.compile(r"^(?:character|char)\((?P<length>\d+)\)$")
_NUMERIC_PATTERN = re.compile(r"^(?:numeric|decimal)\((?P<precision>\d+),(?P<scale>\d+)\)$")
_MYSQL_INT_PATTERN = re.compile(
    r"^(?P<base>tinyint|smallint|mediumint|int|integer|bigint)(?:\((?P<display_width>\d+)\))?(?P<unsigned> unsigned)?$"
)
_MYSQL_TEXT_PATTERN = re.compile(r"^(?:tinytext|text|mediumtext|longtext)$")
_MYSQL_BINARY_PATTERN = re.compile(r"^(?:binary|varbinary|tinyblob|blob|mediumblob|longblob)$")
_TEMPORAL_PRECISION_PATTERN = re.compile(
    r"^(?P<base>timestamp with time zone|timestamp without time zone|timestamp|timestamptz|time with time zone|time without time zone|time|timetz)\((?P<precision>\d+)\)$"
)
_MYSQL_TEMPORAL_PRECISION_PATTERN = re.compile(
    r"^(?P<base>datetime|timestamp|time)\((?P<precision>\d+)\)$"
)


def postgres_type_to_common(source_type: str, *, is_generated: bool = False) -> CommonType:
    normalized_type = _normalize_source_type(source_type)
    base_type = _map_normalized_postgres_type(normalized_type)

    return _with_generated_column_warning(base_type, is_generated=is_generated)


def mysql_type_to_common(source_type: str, *, is_generated: bool = False) -> CommonType:
    normalized_type = _normalize_source_type(source_type)
    base_type = _map_normalized_mysql_type(normalized_type)
    return _with_generated_column_warning(base_type, is_generated=is_generated)


def _with_generated_column_warning(base_type: CommonType, *, is_generated: bool) -> CommonType:
    if not is_generated:
        return base_type
    generated_warning = SchemaWarning(
        code="generated_column_manual_review",
        message="Generated column expression requires manual review before target DDL execution.",
        policy=TypePolicy.MANUAL_REVIEW,
    )
    return CommonType(
        kind=base_type.kind,
        policy=TypePolicy.MANUAL_REVIEW,
        length=base_type.length,
        precision=base_type.precision,
        scale=base_type.scale,
        source_type=base_type.source_type,
        warnings=base_type.warnings + (generated_warning,),
    )


def common_type_to_mysql(common_type: CommonType) -> str:
    match common_type.kind:
        case CommonTypeKind.STRING:
            length = common_type.length or 255
            return f"varchar({length})"
        case CommonTypeKind.TEXT:
            return "longtext"
        case CommonTypeKind.INTEGER:
            return "int"
        case CommonTypeKind.BIGINT:
            return "bigint"
        case CommonTypeKind.SMALLINT:
            return "smallint"
        case CommonTypeKind.DECIMAL:
            if common_type.precision is not None and common_type.scale is not None:
                return f"decimal({common_type.precision},{common_type.scale})"
            return "decimal"
        case CommonTypeKind.BOOLEAN:
            return "tinyint(1)"
        case CommonTypeKind.DATETIME:
            return _temporal_mysql_type("datetime", common_type.precision)
        case CommonTypeKind.TIME:
            return _temporal_mysql_type("time", common_type.precision)
        case CommonTypeKind.DATE:
            return "date"
        case CommonTypeKind.JSON:
            return "json"
        case CommonTypeKind.BINARY:
            return "longblob"
        case CommonTypeKind.UUID:
            return "char(36)"
        case CommonTypeKind.UNKNOWN:
            return "longtext"


def common_type_to_postgres(common_type: CommonType) -> str:
    match common_type.kind:
        case CommonTypeKind.STRING:
            length = common_type.length or 255
            return f"varchar({length})"
        case CommonTypeKind.TEXT:
            return "text"
        case CommonTypeKind.INTEGER:
            return "integer"
        case CommonTypeKind.BIGINT:
            return "bigint"
        case CommonTypeKind.SMALLINT:
            return "smallint"
        case CommonTypeKind.DECIMAL:
            if common_type.precision is not None and common_type.scale is not None:
                return f"numeric({common_type.precision},{common_type.scale})"
            return "numeric"
        case CommonTypeKind.BOOLEAN:
            return "boolean"
        case CommonTypeKind.DATETIME:
            return _temporal_postgres_type("timestamp", common_type.precision)
        case CommonTypeKind.TIME:
            return _temporal_postgres_type("time", common_type.precision)
        case CommonTypeKind.DATE:
            return "date"
        case CommonTypeKind.JSON:
            return "jsonb"
        case CommonTypeKind.BINARY:
            return "bytea"
        case CommonTypeKind.UUID:
            return "uuid"
        case CommonTypeKind.UNKNOWN:
            return "text"


def _normalize_source_type(source_type: str) -> str:
    return " ".join(source_type.strip().lower().split())


def _map_normalized_postgres_type(normalized_type: str) -> CommonType:
    if varchar_match := _VARCHAR_PATTERN.match(normalized_type):
        return CommonType(
            kind=CommonTypeKind.STRING,
            policy=TypePolicy.AUTO_CONVERT,
            length=int(varchar_match.group("length")),
            source_type=normalized_type,
        )

    if char_match := _CHAR_PATTERN.match(normalized_type):
        return CommonType(
            kind=CommonTypeKind.STRING,
            policy=TypePolicy.AUTO_CONVERT,
            length=int(char_match.group("length")),
            source_type=normalized_type,
        )

    if numeric_match := _NUMERIC_PATTERN.match(normalized_type):
        return CommonType(
            kind=CommonTypeKind.DECIMAL,
            policy=TypePolicy.AUTO_CONVERT,
            precision=int(numeric_match.group("precision")),
            scale=int(numeric_match.group("scale")),
            source_type=normalized_type,
        )

    if temporal_match := _TEMPORAL_PRECISION_PATTERN.match(normalized_type):
        return _map_temporal_type(
            temporal_match.group("base"),
            precision=int(temporal_match.group("precision")),
        )

    simple_mappings: dict[str, tuple[CommonTypeKind, TypePolicy]] = {
        "text": (CommonTypeKind.TEXT, TypePolicy.AUTO_CONVERT),
        "integer": (CommonTypeKind.INTEGER, TypePolicy.AUTO_CONVERT),
        "int": (CommonTypeKind.INTEGER, TypePolicy.AUTO_CONVERT),
        "int4": (CommonTypeKind.INTEGER, TypePolicy.AUTO_CONVERT),
        "bigint": (CommonTypeKind.BIGINT, TypePolicy.AUTO_CONVERT),
        "int8": (CommonTypeKind.BIGINT, TypePolicy.AUTO_CONVERT),
        "smallint": (CommonTypeKind.SMALLINT, TypePolicy.AUTO_CONVERT),
        "int2": (CommonTypeKind.SMALLINT, TypePolicy.AUTO_CONVERT),
        "boolean": (CommonTypeKind.BOOLEAN, TypePolicy.AUTO_CONVERT),
        "bool": (CommonTypeKind.BOOLEAN, TypePolicy.AUTO_CONVERT),
        "timestamp without time zone": (CommonTypeKind.DATETIME, TypePolicy.AUTO_CONVERT),
        "timestamp": (CommonTypeKind.DATETIME, TypePolicy.AUTO_CONVERT),
        "time without time zone": (CommonTypeKind.TIME, TypePolicy.AUTO_CONVERT),
        "time": (CommonTypeKind.TIME, TypePolicy.AUTO_CONVERT),
        "date": (CommonTypeKind.DATE, TypePolicy.AUTO_CONVERT),
        "json": (CommonTypeKind.JSON, TypePolicy.AUTO_CONVERT),
        "bytea": (CommonTypeKind.BINARY, TypePolicy.AUTO_CONVERT),
    }
    if normalized_type in simple_mappings:
        kind, policy = simple_mappings[normalized_type]
        return CommonType(kind=kind, policy=policy, source_type=normalized_type)

    warning_mappings: dict[str, tuple[CommonTypeKind, str, str]] = {
        "timestamp with time zone": (
            CommonTypeKind.DATETIME,
            "timestamp_timezone_warning",
            "PostgreSQL timestamp with time zone is converted to MySQL datetime and needs timezone review.",
        ),
        "timestamptz": (
            CommonTypeKind.DATETIME,
            "timestamp_timezone_warning",
            "PostgreSQL timestamptz is converted to MySQL datetime and needs timezone review.",
        ),
        "time with time zone": (
            CommonTypeKind.TIME,
            "time_timezone_warning",
            "PostgreSQL time with time zone is converted to MySQL time and needs timezone review.",
        ),
        "timetz": (
            CommonTypeKind.TIME,
            "time_timezone_warning",
            "PostgreSQL timetz is converted to MySQL time and needs timezone review.",
        ),
        "jsonb": (
            CommonTypeKind.JSON,
            "jsonb_semantic_warning",
            "PostgreSQL jsonb semantic differences require review.",
        ),
        "uuid": (
            CommonTypeKind.UUID,
            "uuid_string_warning",
            "PostgreSQL uuid is converted to MySQL char(36).",
        ),
    }
    if normalized_type in warning_mappings:
        kind, code, message = warning_mappings[normalized_type]
        return CommonType(
            kind=kind,
            policy=TypePolicy.WARN_CONVERT,
            source_type=normalized_type,
            warnings=(SchemaWarning(code=code, message=message, policy=TypePolicy.WARN_CONVERT),),
        )

    if normalized_type.endswith("[]"):
        return CommonType(
            kind=CommonTypeKind.TEXT,
            policy=TypePolicy.MANUAL_REVIEW,
            source_type=normalized_type,
            warnings=(
                SchemaWarning(
                    code="array_manual_review",
                    message="PostgreSQL array type requires manual review.",
                    policy=TypePolicy.MANUAL_REVIEW,
                ),
            ),
        )

    return CommonType(
        kind=CommonTypeKind.UNKNOWN,
        policy=TypePolicy.MANUAL_REVIEW,
        source_type=normalized_type,
        warnings=(
            SchemaWarning(
                code="unknown_type_manual_review",
                message=f"PostgreSQL type requires manual review: {normalized_type}",
                policy=TypePolicy.MANUAL_REVIEW,
            ),
        ),
    )


def _map_normalized_mysql_type(normalized_type: str) -> CommonType:
    if varchar_match := _VARCHAR_PATTERN.match(normalized_type):
        return CommonType(
            kind=CommonTypeKind.STRING,
            policy=TypePolicy.AUTO_CONVERT,
            length=int(varchar_match.group("length")),
            source_type=normalized_type,
        )

    if char_match := _CHAR_PATTERN.match(normalized_type):
        return CommonType(
            kind=CommonTypeKind.STRING,
            policy=TypePolicy.AUTO_CONVERT,
            length=int(char_match.group("length")),
            source_type=normalized_type,
        )

    if numeric_match := _NUMERIC_PATTERN.match(normalized_type):
        return CommonType(
            kind=CommonTypeKind.DECIMAL,
            policy=TypePolicy.AUTO_CONVERT,
            precision=int(numeric_match.group("precision")),
            scale=int(numeric_match.group("scale")),
            source_type=normalized_type,
        )

    if temporal_match := _MYSQL_TEMPORAL_PRECISION_PATTERN.match(normalized_type):
        return _map_mysql_temporal_type(
            temporal_match.group("base"),
            precision=int(temporal_match.group("precision")),
        )

    if int_match := _MYSQL_INT_PATTERN.match(normalized_type):
        return _map_mysql_integer_type(
            int_match.group("base"),
            display_width=int_match.group("display_width"),
            is_unsigned=bool(int_match.group("unsigned")),
            source_type=normalized_type,
        )

    if _MYSQL_TEXT_PATTERN.match(normalized_type):
        return CommonType(kind=CommonTypeKind.TEXT, policy=TypePolicy.AUTO_CONVERT, source_type=normalized_type)

    if _MYSQL_BINARY_PATTERN.match(normalized_type):
        return CommonType(kind=CommonTypeKind.BINARY, policy=TypePolicy.AUTO_CONVERT, source_type=normalized_type)

    simple_mappings: dict[str, tuple[CommonTypeKind, TypePolicy]] = {
        "decimal": (CommonTypeKind.DECIMAL, TypePolicy.AUTO_CONVERT),
        "numeric": (CommonTypeKind.DECIMAL, TypePolicy.AUTO_CONVERT),
        "double": (CommonTypeKind.DECIMAL, TypePolicy.WARN_CONVERT),
        "double precision": (CommonTypeKind.DECIMAL, TypePolicy.WARN_CONVERT),
        "float": (CommonTypeKind.DECIMAL, TypePolicy.WARN_CONVERT),
        "date": (CommonTypeKind.DATE, TypePolicy.AUTO_CONVERT),
        "datetime": (CommonTypeKind.DATETIME, TypePolicy.AUTO_CONVERT),
        "timestamp": (CommonTypeKind.DATETIME, TypePolicy.WARN_CONVERT),
        "time": (CommonTypeKind.TIME, TypePolicy.AUTO_CONVERT),
        "json": (CommonTypeKind.JSON, TypePolicy.AUTO_CONVERT),
        "bool": (CommonTypeKind.BOOLEAN, TypePolicy.AUTO_CONVERT),
        "boolean": (CommonTypeKind.BOOLEAN, TypePolicy.AUTO_CONVERT),
    }
    if normalized_type in simple_mappings:
        kind, policy = simple_mappings[normalized_type]
        warnings = ()
        if policy is TypePolicy.WARN_CONVERT:
            warnings = (
                SchemaWarning(
                    code="mysql_type_semantic_warning",
                    message=f"MySQL type requires semantic review before PostgreSQL conversion: {normalized_type}",
                    policy=TypePolicy.WARN_CONVERT,
                ),
            )
        return CommonType(kind=kind, policy=policy, source_type=normalized_type, warnings=warnings)

    if normalized_type.startswith("enum(") or normalized_type.startswith("set("):
        return CommonType(
            kind=CommonTypeKind.TEXT,
            policy=TypePolicy.MANUAL_REVIEW,
            source_type=normalized_type,
            warnings=(
                SchemaWarning(
                    code="mysql_enum_set_manual_review",
                    message="MySQL enum/set type requires manual review before PostgreSQL conversion.",
                    policy=TypePolicy.MANUAL_REVIEW,
                ),
            ),
        )

    return CommonType(
        kind=CommonTypeKind.UNKNOWN,
        policy=TypePolicy.MANUAL_REVIEW,
        source_type=normalized_type,
        warnings=(
            SchemaWarning(
                code="unknown_type_manual_review",
                message=f"MySQL type requires manual review: {normalized_type}",
                policy=TypePolicy.MANUAL_REVIEW,
            ),
        ),
    )


def _map_mysql_integer_type(
    base_type: str,
    *,
    display_width: str | None,
    is_unsigned: bool,
    source_type: str,
) -> CommonType:
    if base_type == "tinyint":
        kind = CommonTypeKind.SMALLINT if is_unsigned or display_width != "1" else CommonTypeKind.BOOLEAN
    elif base_type in {"smallint", "mediumint"}:
        kind = CommonTypeKind.INTEGER if is_unsigned or base_type == "mediumint" else CommonTypeKind.SMALLINT
    elif base_type in {"int", "integer"}:
        kind = CommonTypeKind.BIGINT if is_unsigned else CommonTypeKind.INTEGER
    else:
        kind = CommonTypeKind.DECIMAL if is_unsigned else CommonTypeKind.BIGINT

    if is_unsigned:
        return CommonType(
            kind=kind,
            policy=TypePolicy.WARN_CONVERT,
            source_type=source_type,
            warnings=(
                SchemaWarning(
                    code="mysql_unsigned_warning",
                    message="MySQL unsigned integer range requires review before PostgreSQL conversion.",
                    policy=TypePolicy.WARN_CONVERT,
                ),
            ),
        )
    return CommonType(kind=kind, policy=TypePolicy.AUTO_CONVERT, source_type=source_type)


def _map_mysql_temporal_type(base_type: str, *, precision: int) -> CommonType:
    kind = CommonTypeKind.TIME if base_type == "time" else CommonTypeKind.DATETIME
    policy = TypePolicy.WARN_CONVERT if base_type == "timestamp" else TypePolicy.AUTO_CONVERT
    warnings = ()
    if policy is TypePolicy.WARN_CONVERT:
        warnings = (
            SchemaWarning(
                code="mysql_timestamp_timezone_warning",
                message="MySQL timestamp timezone behavior requires review before PostgreSQL conversion.",
                policy=TypePolicy.WARN_CONVERT,
            ),
        )
    return CommonType(kind=kind, policy=policy, precision=precision, source_type=f"{base_type}({precision})", warnings=warnings)


def _map_temporal_type(base_type: str, *, precision: int) -> CommonType:
    if base_type in {"timestamp with time zone", "timestamptz"}:
        return CommonType(
            kind=CommonTypeKind.DATETIME,
            policy=TypePolicy.WARN_CONVERT,
            precision=precision,
            source_type=f"{base_type}({precision})",
            warnings=(
                SchemaWarning(
                    code="timestamp_timezone_warning",
                    message="PostgreSQL timestamp with time zone is converted to MySQL datetime and needs timezone review.",
                    policy=TypePolicy.WARN_CONVERT,
                ),
            ),
        )
    if base_type in {"time with time zone", "timetz"}:
        return CommonType(
            kind=CommonTypeKind.TIME,
            policy=TypePolicy.WARN_CONVERT,
            precision=precision,
            source_type=f"{base_type}({precision})",
            warnings=(
                SchemaWarning(
                    code="time_timezone_warning",
                    message="PostgreSQL time with time zone is converted to MySQL time and needs timezone review.",
                    policy=TypePolicy.WARN_CONVERT,
                ),
            ),
        )
    if base_type in {"time without time zone", "time"}:
        return CommonType(
            kind=CommonTypeKind.TIME,
            policy=TypePolicy.AUTO_CONVERT,
            precision=precision,
            source_type=f"{base_type}({precision})",
        )
    return CommonType(
        kind=CommonTypeKind.DATETIME,
        policy=TypePolicy.AUTO_CONVERT,
        precision=precision,
        source_type=f"{base_type}({precision})",
    )


def _temporal_mysql_type(type_name: str, precision: int | None) -> str:
    if precision is None:
        return type_name
    return f"{type_name}({precision})"


def _temporal_postgres_type(type_name: str, precision: int | None) -> str:
    if precision is None:
        return type_name
    return f"{type_name}({precision})"
