# Sample Configs

## Full Migration

```yaml
job:
  name: legacy-postgres-to-mysql
source:
  dbms: postgresql
  host: localhost
  port: 5432
  database: legacy
  schema: public
  user: readonly_user
  password: null
target:
  dbms: mysql
  host: localhost
  port: 3306
  database: migrated
  user: migration_user
  password: null
  environment: staging
migration:
  mode: ddl_and_dml
  existing_table_policy: skip
  batch_size: 10000
  commit_interval: 10000
safety:
  is_production_protection: true
report:
  output_dir: ./reports
```

## Incremental Migration

```yaml
job:
  name: legacy-postgres-to-mysql-incremental
incremental:
  enabled: true
  delete_sync: false
  watermarks:
    users:
      column: updated_at
      start_value: "2026-01-01T00:00:00"
      end_value: "2026-02-01T00:00:00"
```

DELETE sync is intentionally excluded from automatic execution and appears in incremental reports as a manual follow-up.
