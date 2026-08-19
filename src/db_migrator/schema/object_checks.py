from __future__ import annotations

from dataclasses import dataclass

from db_migrator.config.models import Dbms
from db_migrator.schema.dialect import qualified_table_name, quote_identifier
from db_migrator.schema.models import ColumnSchema, ForeignKeySchema, IndexSchema, SchemaObjectKind, SchemaObjectSummary, SchemaSnapshot, TableRef, TableSchema


@dataclass(frozen=True)
class IndexPlanItem:
    schema: str
    table: str
    name: str
    columns: tuple[str, ...]
    unique: bool
    status: str
    action: str
    target_ddl: str | None = None
    reason: str | None = None


@dataclass(frozen=True)
class SchemaObjectComparison:
    object_type: str
    object_name: str
    status: str
    source_detail: str
    target_detail: str
    action: str


def build_index_plan(snapshot: SchemaSnapshot, *, target_dbms: Dbms) -> tuple[IndexPlanItem, ...]:
    return tuple(
        _build_index_plan_item(table, index, target_dbms=target_dbms)
        for table in snapshot.tables
        for index in table.indexes
    )


def compare_schema_objects(source: SchemaSnapshot, target: SchemaSnapshot) -> tuple[SchemaObjectComparison, ...]:
    comparisons = list(_compare_table_definitions(source, target))
    comparisons.extend(_compare_auto_increment_columns(source, target))

    source_indexes = {_index_key(table.ref, index): (table.ref, index) for table in source.tables for index in table.indexes}
    target_indexes = {_index_key(table.ref, index): (table.ref, index) for table in target.tables for index in table.indexes}
    comparisons.extend(_compare_index(key, source_indexes.get(key), target_indexes.get(key)) for key in sorted(set(source_indexes) | set(target_indexes)))

    source_foreign_keys = {_foreign_key_key(table.ref, foreign_key): (table.ref, foreign_key) for table in source.tables for foreign_key in table.foreign_keys}
    target_foreign_keys = {_foreign_key_key(table.ref, foreign_key): (table.ref, foreign_key) for table in target.tables for foreign_key in table.foreign_keys}
    comparisons.extend(
        _compare_foreign_key(key, source_foreign_keys.get(key), target_foreign_keys.get(key))
        for key in sorted(set(source_foreign_keys) | set(target_foreign_keys))
    )

    source_objects = {_object_key(schema_object): schema_object for schema_object in source.non_table_objects}
    target_objects = {_object_key(schema_object): schema_object for schema_object in target.non_table_objects}
    comparisons.extend(
        _compare_schema_object(key, source_objects.get(key), target_objects.get(key))
        for key in sorted(set(source_objects) | set(target_objects))
    )
    return tuple(comparison for comparison in comparisons if comparison is not None)


def _compare_table_definitions(source: SchemaSnapshot, target: SchemaSnapshot) -> tuple[SchemaObjectComparison, ...]:
    source_tables = {(table.ref.schema, table.ref.name): table for table in source.tables}
    target_tables = {(table.ref.schema, table.ref.name): table for table in target.tables}
    comparisons: list[SchemaObjectComparison] = []
    for key in sorted(set(source_tables) | set(target_tables)):
        source_table = source_tables.get(key)
        target_table = target_tables.get(key)
        comparisons.extend(_compare_table_presence(key, source_table, target_table))
        if source_table is None or target_table is None:
            continue
        comparisons.extend(_compare_columns(source_table, target_table))
        comparisons.append(_compare_primary_key(source_table, target_table))
    return tuple(comparison for comparison in comparisons if comparison is not None)


def _compare_table_presence(
    key: tuple[str, str],
    source_table: TableSchema | None,
    target_table: TableSchema | None,
) -> tuple[SchemaObjectComparison, ...]:
    object_name = ".".join(key)
    if source_table is None:
        return (
            SchemaObjectComparison(
                object_type="테이블",
                object_name=object_name,
                status="target_only",
                source_detail="-",
                target_detail="있음",
                action="source 후보에 없는 target 테이블입니다. 기존 target 잔여 테이블인지 확인하세요.",
            ),
        )
    if target_table is None:
        return (
            SchemaObjectComparison(
                object_type="테이블",
                object_name=object_name,
                status="missing",
                source_detail=_table_detail(source_table),
                target_detail="-",
                action="source 이관 후보 테이블이 target에 없습니다. apply-ddl 실행 결과와 대상 스키마를 확인하세요.",
            ),
        )
    if len(source_table.columns) == len(target_table.columns):
        status = "matched"
        action = "테이블 존재 여부가 일치합니다. 컬럼/PK/인덱스/FK 상세 비교 결과를 함께 확인하세요."
    else:
        status = "mismatched"
        action = "source 후보와 target 실제 테이블의 컬럼 수가 다릅니다. 컬럼별 비교 결과와 실행 DDL을 확인하세요."
    return (
        SchemaObjectComparison(
            object_type="테이블",
            object_name=object_name,
            status=status,
            source_detail=_table_detail(source_table),
            target_detail=_table_detail(target_table),
            action=action,
        ),
    )


def _compare_columns(source_table: TableSchema, target_table: TableSchema) -> tuple[SchemaObjectComparison, ...]:
    source_columns = {column.name: column for column in source_table.columns}
    target_columns = {column.name: column for column in target_table.columns}
    comparisons: list[SchemaObjectComparison] = []
    for column_name in sorted(set(source_columns) | set(target_columns)):
        source_column = source_columns.get(column_name)
        target_column = target_columns.get(column_name)
        object_name = f"{source_table.ref.schema}.{source_table.ref.name}.{column_name}"
        if source_column is None:
            comparisons.append(
                SchemaObjectComparison(
                    object_type="컬럼",
                    object_name=object_name,
                    status="target_only",
                    source_detail="-",
                    target_detail=_column_detail(target_column),
                    action="source 후보에 없는 target 컬럼입니다. 기존 target 잔여 컬럼 또는 컬럼 매핑 결과인지 확인하세요.",
                )
            )
            continue
        if target_column is None:
            comparisons.append(
                SchemaObjectComparison(
                    object_type="컬럼",
                    object_name=object_name,
                    status="missing",
                    source_detail=_column_detail(source_column),
                    target_detail="-",
                    action="source 후보 컬럼이 target에 없습니다. ADD COLUMN 실행 결과와 컬럼 매핑을 확인하세요.",
                )
            )
            continue
        if _column_signature(source_column) == _column_signature(target_column):
            comparisons.append(
                SchemaObjectComparison(
                    object_type="컬럼",
                    object_name=object_name,
                    status="matched",
                    source_detail=_column_detail(source_column),
                    target_detail=_column_detail(target_column),
                    action="추가 조치가 필요하지 않습니다.",
                )
            )
            continue
        comparisons.append(
            SchemaObjectComparison(
                object_type="컬럼",
                object_name=object_name,
                status="mismatched",
                source_detail=_column_detail(source_column),
                target_detail=_column_detail(target_column),
                action="컬럼 타입, nullable, default, generated, auto increment 정의가 source 후보와 target 실제 상태에서 다른지 확인하세요.",
            )
        )
    return tuple(comparisons)


def _compare_primary_key(source_table: TableSchema, target_table: TableSchema) -> SchemaObjectComparison:
    object_name = f"{source_table.ref.schema}.{source_table.ref.name}.PK"
    source_detail = _primary_key_detail(source_table)
    target_detail = _primary_key_detail(target_table)
    status = "matched" if source_detail == target_detail else "mismatched"
    action = "추가 조치가 필요하지 않습니다." if status == "matched" else "PK 컬럼 구성이 source 후보와 target 실제 상태에서 다릅니다. 테이블 생성 DDL을 확인하세요."
    return SchemaObjectComparison(
        object_type="PK",
        object_name=object_name,
        status=status,
        source_detail=source_detail,
        target_detail=target_detail,
        action=action,
    )


def _compare_auto_increment_columns(source: SchemaSnapshot, target: SchemaSnapshot) -> tuple[SchemaObjectComparison, ...]:
    target_tables = {(table.ref.schema, table.ref.name): table for table in target.tables}
    comparisons: list[SchemaObjectComparison] = []
    for source_table in source.tables:
        target_table = target_tables.get((source_table.ref.schema, source_table.ref.name))
        target_columns = {column.name: column for column in target_table.columns} if target_table is not None else {}
        for source_column in source_table.columns:
            if not source_column.auto_increment:
                continue
            target_column = target_columns.get(source_column.name)
            object_name = f"{source_table.ref.schema}.{source_table.ref.name}.{source_column.name}"
            if target_column is None:
                comparisons.append(
                    SchemaObjectComparison(
                        object_type="컬럼 속성",
                        object_name=object_name,
                        status="missing",
                        source_detail="원본 자동증가",
                        target_detail="-",
                        action="대상 테이블/컬럼이 없거나 이름 매핑이 다릅니다. 테이블 생성 SQL과 컬럼 매핑을 확인하세요.",
                    )
                )
                continue
            if target_column.auto_increment:
                comparisons.append(
                    SchemaObjectComparison(
                        object_type="컬럼 속성",
                        object_name=object_name,
                        status="matched",
                        source_detail="원본 자동증가",
                        target_detail="AUTO_INCREMENT",
                        action="추가 조치가 필요하지 않습니다.",
                    )
                )
                continue
            comparisons.append(
                SchemaObjectComparison(
                    object_type="컬럼 속성",
                    object_name=object_name,
                    status="mismatched",
                    source_detail="원본 자동증가",
                    target_detail="AUTO_INCREMENT 누락",
                    action="대상 컬럼에 AUTO_INCREMENT가 누락되었습니다. ALTER TABLE ... MODIFY ... AUTO_INCREMENT 적용 또는 테이블 생성 SQL 재생성을 검토하세요.",
                )
            )
    return tuple(comparisons)


def _build_index_plan_item(table: TableSchema, index: IndexSchema, *, target_dbms: Dbms) -> IndexPlanItem:
    if not is_auto_create_index(index):
        return IndexPlanItem(
            schema=table.ref.schema,
            table=table.ref.name,
            name=index.name,
            columns=index.columns,
            unique=index.unique,
            status="manual_review",
            reason=index.manual_review_reason or "Index definition requires manual review.",
            action="대상 DBMS 기준으로 인덱스 정의와 실행 시점을 수동 검토하세요.",
        )
    return IndexPlanItem(
        schema=table.ref.schema,
        table=table.ref.name,
        name=index.name,
        columns=index.columns,
        unique=index.unique,
        status="auto_candidate",
        target_ddl=create_index_ddl(table.ref, index, target_dbms=target_dbms),
        action="데이터 이관 완료 후 target DB에 별도 CREATE INDEX 후보로 적용할 수 있습니다.",
    )


def _compare_index(
    key: tuple[str, str, str],
    source_item: tuple[TableRef, IndexSchema] | None,
    target_item: tuple[TableRef, IndexSchema] | None,
) -> SchemaObjectComparison | None:
    if source_item is None and target_item is None:
        return None
    if source_item is None:
        target_ref, target_index = target_item or (TableRef("", ""), IndexSchema("", ()))
        return SchemaObjectComparison(
            object_type="인덱스",
            object_name=_index_label(target_ref, target_index),
            status="target_only",
            source_detail="-",
            target_detail=_index_detail(target_index),
            action="source에 없는 target 인덱스입니다. 기존 target 잔여 객체인지 확인하세요.",
        )
    if target_item is None:
        source_ref, source_index = source_item
        if not is_auto_create_index(source_index):
            return SchemaObjectComparison(
                object_type="인덱스",
                object_name=_index_label(source_ref, source_index),
                status="manual_review",
                source_detail=_index_detail(source_index),
                target_detail="-",
                action=(
                    source_index.manual_review_reason
                    or "자동 생성 후보가 아닌 인덱스입니다. 대상 DBMS 기준 정의와 적용 여부를 수동 검토하세요."
                ),
            )
        return SchemaObjectComparison(
            object_type="인덱스",
            object_name=_index_label(source_ref, source_index),
            status="missing",
            source_detail=_index_detail(source_index),
            target_detail="-",
            action="이관 완료 후 CREATE INDEX 적용 여부를 확인하세요.",
        )

    source_ref, source_index = source_item
    _, target_index = target_item
    if _index_signature(source_index) == _index_signature(target_index):
        return SchemaObjectComparison(
            object_type="인덱스",
            object_name=_index_label(source_ref, source_index),
            status="matched",
            source_detail=_index_detail(source_index),
            target_detail=_index_detail(target_index),
            action="추가 조치가 필요하지 않습니다.",
        )
    return SchemaObjectComparison(
        object_type="인덱스",
        object_name=_index_label(source_ref, source_index),
        status="mismatched",
        source_detail=_index_detail(source_index),
        target_detail=_index_detail(target_index),
        action="unique 여부, 컬럼 순서, 인덱스 타입을 source 기준과 맞춰 재검토하세요.",
    )


def _compare_foreign_key(
    key: tuple[str, str, str],
    source_item: tuple[TableRef, ForeignKeySchema] | None,
    target_item: tuple[TableRef, ForeignKeySchema] | None,
) -> SchemaObjectComparison | None:
    if source_item is None and target_item is None:
        return None
    if source_item is None:
        target_ref, target_foreign_key = target_item or (TableRef("", ""), ForeignKeySchema("", (), TableRef("", ""), ()))
        return SchemaObjectComparison(
            object_type="FK",
            object_name=_foreign_key_label(target_ref, target_foreign_key),
            status="target_only",
            source_detail="-",
            target_detail=_foreign_key_detail(target_foreign_key),
            action="source에 없는 target FK입니다. 기존 target 잔여 제약인지 확인하세요.",
        )
    if target_item is None:
        source_ref, source_foreign_key = source_item
        return SchemaObjectComparison(
            object_type="FK",
            object_name=_foreign_key_label(source_ref, source_foreign_key),
            status="missing",
            source_detail=_foreign_key_detail(source_foreign_key),
            target_detail="-",
            action="apply-foreign-keys 실행 결과와 target 제약 조건을 확인하세요.",
        )

    source_ref, source_foreign_key = source_item
    _, target_foreign_key = target_item
    if _foreign_key_signature(source_foreign_key) == _foreign_key_signature(target_foreign_key):
        return SchemaObjectComparison(
            object_type="FK",
            object_name=_foreign_key_label(source_ref, source_foreign_key),
            status="matched",
            source_detail=_foreign_key_detail(source_foreign_key),
            target_detail=_foreign_key_detail(target_foreign_key),
            action="추가 조치가 필요하지 않습니다.",
        )
    return SchemaObjectComparison(
        object_type="FK",
        object_name=_foreign_key_label(source_ref, source_foreign_key),
        status="mismatched",
        source_detail=_foreign_key_detail(source_foreign_key),
        target_detail=_foreign_key_detail(target_foreign_key),
        action="FK 컬럼, 참조 테이블, 참조 컬럼 순서를 source 기준과 맞춰 재검토하세요.",
    )


def _compare_schema_object(
    key: tuple[str, str, str],
    source_object: SchemaObjectSummary | None,
    target_object: SchemaObjectSummary | None,
) -> SchemaObjectComparison | None:
    if source_object is None and target_object is None:
        return None
    object_type = _object_type_label(SchemaObjectKind(key[0]))
    object_name = ".".join(key[1:])
    if source_object is None:
        return SchemaObjectComparison(
            object_type=object_type,
            object_name=object_name,
            status="target_only",
            source_detail="-",
            target_detail="있음",
            action="source에 없는 target 객체입니다. 기존 target 잔여 객체인지 확인하세요.",
        )
    if target_object is None:
        return SchemaObjectComparison(
            object_type=object_type,
            object_name=object_name,
            status="missing",
            source_detail="있음",
            target_detail="-",
            action=f"{object_type}는 자동 변환 대상이 아닙니다. 대상 DBMS 기준 정의를 수동 반영했는지 확인하세요.",
        )
    return SchemaObjectComparison(
        object_type=object_type,
        object_name=object_name,
        status="matched",
        source_detail="있음",
        target_detail="있음",
        action="객체 존재 여부는 일치합니다. 본문 의미 검증은 수동 검토하세요.",
    )


def is_auto_create_index(index: IndexSchema) -> bool:
    return bool(index.columns) and index.auto_create_candidate and index.manual_review_reason is None


def create_index_ddl(
    table: TableRef,
    index: IndexSchema,
    *,
    target_dbms: Dbms,
    target_database: str | None = None,
) -> str:
    quote = lambda value: quote_identifier(target_dbms, value)
    unique = "UNIQUE " if index.unique else ""
    schema_or_database = target_database if target_dbms in {Dbms.MYSQL, Dbms.MARIADB} and target_database else table.schema
    table_name = qualified_table_name(target_dbms, schema_or_database, table.name)
    columns = ", ".join(quote(column) for column in index.columns)
    return f"CREATE {unique}INDEX {quote(index.name)} ON {table_name} ({columns});"


def _index_key(table: TableRef, index: IndexSchema) -> tuple[str, str, str]:
    return (table.schema, table.name, index.name)


def _foreign_key_key(table: TableRef, foreign_key: ForeignKeySchema) -> tuple[str, str, str]:
    return (table.schema, table.name, foreign_key.name)


def _object_key(schema_object: SchemaObjectSummary) -> tuple[str, str, str]:
    return (schema_object.kind.value, schema_object.schema, schema_object.name)


def _index_signature(index: IndexSchema) -> tuple[tuple[str, ...], bool, str | None]:
    return (index.columns, index.unique, index.method.lower() if index.method else None)


def _index_label(table: TableRef, index: IndexSchema) -> str:
    return f"{table.schema}.{table.name}.{index.name}"


def _index_detail(index: IndexSchema) -> str:
    unique = "unique" if index.unique else "non-unique"
    method = f", method={index.method}" if index.method else ""
    return f"{unique}, columns=({', '.join(index.columns)}){method}"


def _foreign_key_signature(foreign_key: ForeignKeySchema) -> tuple[tuple[str, ...], tuple[str, str], tuple[str, ...]]:
    return (foreign_key.columns, (foreign_key.referenced_table.schema, foreign_key.referenced_table.name), foreign_key.referenced_columns)


def _foreign_key_label(table: TableRef, foreign_key: ForeignKeySchema) -> str:
    return f"{table.schema}.{table.name}.{foreign_key.name}"


def _foreign_key_detail(foreign_key: ForeignKeySchema) -> str:
    referenced_table = f"{foreign_key.referenced_table.schema}.{foreign_key.referenced_table.name}"
    return (
        f"columns=({', '.join(foreign_key.columns)}), "
        f"references={referenced_table}({', '.join(foreign_key.referenced_columns)})"
    )


def _table_detail(table: TableSchema) -> str:
    primary_key = _primary_key_detail(table)
    return f"columns={len(table.columns)}, pk={primary_key}, indexes={len(table.indexes)}, fks={len(table.foreign_keys)}"


def _column_detail(column: ColumnSchema | None) -> str:
    if column is None:
        return "-"
    nullable = "nullable" if column.nullable else "not null"
    default = f", default={column.default}" if column.default is not None else ""
    generated = ", generated" if column.is_generated else ""
    auto_increment = ", auto_increment" if column.auto_increment else ""
    return f"type={column.common_type.kind.value}, source_type={column.source_type}, {nullable}{default}{generated}{auto_increment}"


def _column_signature(column: ColumnSchema) -> tuple[str, bool, str | None, bool, str | None]:
    return (
        column.common_type.kind.value,
        column.nullable,
        column.default,
        column.is_generated,
        column.generation_expression,
    )


def _primary_key_detail(table: TableSchema) -> str:
    if table.primary_key is None or not table.primary_key.columns:
        return "-"
    return f"({', '.join(table.primary_key.columns)})"


def _object_type_label(kind: SchemaObjectKind) -> str:
    return {
        SchemaObjectKind.VIEW: "뷰",
        SchemaObjectKind.FUNCTION: "함수",
        SchemaObjectKind.PROCEDURE: "프로시저",
        SchemaObjectKind.TRIGGER: "트리거",
    }[kind]

