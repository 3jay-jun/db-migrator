# CLI Reference

Generated command reference for the current harness.

| Command | Purpose |
| --- | --- |
| `db-migrator doctor` | Check local runtime, required imports, writable output directories, and optional tools. |
| `db-migrator dry-run` | Generate DDL/risk reports without writing to target DB. |
| `db-migrator apply-ddl` | Execute target DDL after Safety Guard checks. |
| `db-migrator migrate-data` | Run v1.0 full DML batch migration. |
| `db-migrator resume` | Resume from checkpoint state. |
| `db-migrator retry-failed` | Retry only failed tables from checkpoint state. |
| `db-migrator validate` | Run row count and checksum sample validation. |
| `db-migrator migrate-incremental` | Run v1.1 watermark + upsert incremental migration. |
| `db-migrator self-test run` | Run Docker-based source seed, DDL, DML, and validation self-test. |
| `db-migrator package-check` | Check PyInstaller availability. |

Use `uv run db-migrator <command> --help` for exact options.
