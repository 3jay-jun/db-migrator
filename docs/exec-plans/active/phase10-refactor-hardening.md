# Phase 10: Refactor Hardening

## Scope

Phase 10 addresses post-v1 hardening items found during the refactoring audit. The first pass is intentionally narrow: fix runtime-risk issues before larger structural cleanup.

## SSOT Search

- Search command: `rg -n "incremental|commit_interval|_load_target_snapshot|target_schema_reader|parallel_table_count|target_lock|_writable_columns|resume_key_columns|_upsert_keys" src tests docs\exec-plans`
- Result: incremental commit accounting, target schema scan lifecycle, parallel DML locking, writable column selection, and key selection have existing implementations. Reuse or extract these instead of adding parallel rules.
- Search command: `rg -n "quote_mysql_identifier|quote_postgres_identifier|_quote_mysql_identifier|_quote_postgres_identifier|_mysql_quote|_postgres_quote|_qualified_table_name|qualified_table_name" src tests`
- Result: identifier quoting existed in adapters, core/manual_migration.py, core/foreign_keys.py, schema/column_plan.py, schema/object_checks.py, and GUI preview. Centralize implementation in schema/dialect.py while preserving adapter public imports.

## Checklist

- [x] Fix incremental migration commit interval accounting so commits are based on uncommitted rows.
- [x] Ensure target schema scan adapters are closed after metadata reads.
- [x] Add regression tests for incremental commit interval and target schema scan adapter cleanup.
- [x] Review parallel DML locking and document the next refactor boundary if it is not changed in this pass.
- [x] Add worker-specific target adapter factory for application-level parallel DML writes.
- [x] Extract writable column and key selection rules into schema table-selection SSOT.
- [x] Extract identifier quoting and qualified table naming into schema dialect SSOT.
- [x] Remove unused placeholder modules, legacy self-test samples, and unused pydantic-settings dependency.
- [x] Include GUI root PNG assets in package data.
- [x] Run focused tests.
- [x] Run full unit test suite.

## Validation Commands

```text
uv run pytest tests/unit/test_incremental.py tests/unit/test_application_service.py
uv run pytest
uv run pytest tests/unit/test_dialect.py tests/unit/test_mysql_ddl.py tests/unit/test_postgres_ddl.py tests/unit/test_foreign_keys.py tests/unit/test_indexes.py tests/unit/test_schema_pair_column_plan.py tests/unit/test_dry_run_report.py tests/unit/test_application_service.py tests/unit/test_gui_preview.py tests/unit/test_gui_table_grid.py
uv run pytest
uv lock
uv run pytest
```

## Side Effects

The intended code changes affect DB connection lifecycle and target commit timing. They should reduce leaked metadata scan connections and prevent excessive commits during incremental migration without changing batch read/write semantics.
