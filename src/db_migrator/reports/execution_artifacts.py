from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from db_migrator.core.validation import DataSyncArtifact, ExecutionArtifact


def load_execution_artifacts(report_dir: Path) -> tuple[ExecutionArtifact, ...]:
    artifacts: list[ExecutionArtifact] = []
    artifacts.extend(_load_ddl_execution_artifacts(report_dir / "ddl-execution.json"))
    for index_report in sorted(report_dir.glob("index-execution-*.json")):
        artifacts.extend(_load_index_execution_artifacts(index_report))
    artifacts.extend(_load_foreign_key_execution_artifacts(report_dir / "foreign-key-execution.json"))
    return tuple(artifacts)


def load_data_sync_artifacts(report_dir: Path) -> tuple[DataSyncArtifact, ...]:
    payload = _read_json_object(report_dir / "data-sync-execution.json")
    if payload is None:
        return ()
    return tuple(
        DataSyncArtifact(
            schema=str(item.get("schema") or ""),
            table=str(item.get("table") or ""),
            status=str(item.get("status") or ""),
            rows_inserted=_int_value(item.get("rows_inserted")),
            rows_updated=_int_value(item.get("rows_updated")),
            rows_deleted=_int_value(item.get("rows_deleted")),
            rows_unchanged=_int_value(item.get("rows_unchanged")),
            rows_processed=_int_value(item.get("rows_processed")),
            rows_written=_int_value(item.get("rows_written")),
            changed_rows=_int_value(item.get("changed_rows")),
            batches_written=_int_value(item.get("batches_written")),
            message=_optional_string(item.get("message")),
        )
        for item in _list_value(payload.get("tables"))
    )


def _load_ddl_execution_artifacts(report_path: Path) -> tuple[ExecutionArtifact, ...]:
    payload = _read_json_object(report_path)
    if payload is None:
        return ()
    artifacts = [
        ExecutionArtifact(
            artifact_type="DDL",
            object_name=_qualified_name(item.get("schema"), item.get("table")),
            action=str(item.get("action", "-")),
            success=bool(item.get("success", False)),
            message=str(item.get("message") or ""),
            ddl=_optional_string(item.get("ddl")),
            source_file=report_path.name,
        )
        for item in _list_value(payload.get("tables"))
    ]
    artifacts.extend(
        ExecutionArtifact(
            artifact_type="FK",
            object_name=_qualified_name(item.get("schema"), item.get("table")),
            action=str(item.get("action", "-")),
            success=bool(item.get("success", False)),
            message=str(item.get("message") or ""),
            ddl=_optional_string(item.get("ddl")),
            source_file=report_path.name,
        )
        for item in _list_value(payload.get("foreign_keys"))
    )
    return tuple(artifacts)


def _load_index_execution_artifacts(report_path: Path) -> tuple[ExecutionArtifact, ...]:
    payload = _read_json_object(report_path)
    if payload is None:
        return ()
    return tuple(
        ExecutionArtifact(
            artifact_type="INDEX",
            object_name=".".join(
                part
                for part in (
                    _optional_string(item.get("schema")),
                    _optional_string(item.get("table")),
                    _optional_string(item.get("index")),
                )
                if part
            ),
            action=str(item.get("action", payload.get("phase", "-"))),
            success=bool(item.get("success", False)),
            message=str(item.get("message") or ""),
            ddl=_optional_string(item.get("ddl")),
            source_file=report_path.name,
        )
        for item in _list_value(payload.get("indexes"))
    )


def _load_foreign_key_execution_artifacts(report_path: Path) -> tuple[ExecutionArtifact, ...]:
    payload = _read_json_object(report_path)
    if payload is None:
        return ()
    return tuple(
        ExecutionArtifact(
            artifact_type="FK",
            object_name=".".join(
                part
                for part in (
                    _optional_string(item.get("table")),
                    _optional_string(item.get("constraint_name")),
                )
                if part
            ),
            action="add_constraint",
            success=bool(item.get("success", False)),
            message=str(item.get("message") or ""),
            ddl=_optional_string(item.get("ddl")),
            source_file=report_path.name,
        )
        for item in _list_value(payload.get("foreign_keys"))
    )


def _read_json_object(report_path: Path) -> dict[str, Any] | None:
    if not report_path.exists():
        return None
    try:
        payload = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _list_value(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _qualified_name(schema: object, table: object) -> str:
    schema_name = _optional_string(schema)
    table_name = _optional_string(table)
    if schema_name and table_name:
        return f"{schema_name}.{table_name}"
    return table_name or schema_name or "-"


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def _int_value(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0
