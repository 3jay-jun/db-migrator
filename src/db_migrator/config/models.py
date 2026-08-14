from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class Dbms(StrEnum):
    POSTGRESQL = "postgresql"
    MYSQL = "mysql"
    MARIADB = "mariadb"


class TargetEnvironment(StrEnum):
    LOCAL = "local"
    DEV = "dev"
    STAGING = "staging"
    PRODUCTION = "production"


class MigrationMode(StrEnum):
    DDL_ONLY = "ddl_only"
    DML_ONLY = "dml_only"
    DDL_AND_DML = "ddl_and_dml"
    DRY_RUN = "dry_run"
    INCREMENTAL = "incremental"
    MANUAL = "manual"
    MANUAL_DDL = "manual_ddl"


class IndexApplyTiming(StrEnum):
    PRE_DATA = "pre_data"
    POST_DATA = "post_data"
    MANUAL_REVIEW = "manual_review"
    SKIP = "skip"


class ExistingTablePolicy(StrEnum):
    SKIP = "skip"
    COMPARE_ONLY = "compare_only"
    APPEND = "append"
    SYNC = "sync"
    TRUNCATE_RELOAD = "truncate_reload"
    OVERWRITE = "overwrite"


class SourceOnlyColumnAction(StrEnum):
    IGNORE = "ignore"
    MANUAL = "manual"
    ADD_TO_TARGET = "add_to_target"


class JobConfig(BaseModel):
    name: str = Field(default="db-migration-job", min_length=1)


class SshAuthenticationType(StrEnum):
    KEY = "key"
    PASSWORD = "password"


class SshTunnelConfig(BaseModel):
    enabled: bool = False
    ssh_host: str | None = Field(default=None, min_length=1)
    ssh_port: int = Field(default=22, gt=0, le=65535)
    ssh_user: str | None = Field(default=None, min_length=1)
    auth_type: SshAuthenticationType = SshAuthenticationType.KEY
    private_key_path: str | None = Field(default=None, min_length=1)
    private_key_passphrase_env: str | None = Field(default=None, min_length=1)
    ssh_password: str | None = None
    known_hosts_path: str | None = Field(default=None, min_length=1)
    remote_host: str | None = Field(default=None, min_length=1)
    remote_port: int | None = Field(default=None, gt=0, le=65535)
    local_host: str = Field(default="127.0.0.1", min_length=1)
    local_port: int = Field(default=0, ge=0, le=65535)
    keepalive_interval_seconds: float = Field(default=30.0, ge=0)


class SourceConfig(BaseModel):
    dbms: Dbms = Dbms.POSTGRESQL
    host: str = "localhost"
    port: int = Field(default=5432, gt=0, le=65535)
    database: str = Field(default="source", min_length=1)
    schema_name: str = Field(default="public", alias="schema")
    user: str = Field(default="readonly_user", min_length=1)
    password: str | None = None
    tunnel: SshTunnelConfig = Field(default_factory=SshTunnelConfig)


class TargetConfig(BaseModel):
    dbms: Dbms = Dbms.MYSQL
    host: str = "localhost"
    port: int = Field(default=3306, gt=0, le=65535)
    database: str = Field(default="target", min_length=1)
    schema_name: str | None = Field(default=None, alias="schema", min_length=1)
    user: str = Field(default="migration_user", min_length=1)
    password: str | None = None
    environment: TargetEnvironment = TargetEnvironment.STAGING
    tunnel: SshTunnelConfig = Field(default_factory=SshTunnelConfig)


class MigrationConfig(BaseModel):
    mode: MigrationMode = MigrationMode.DRY_RUN
    existing_table_policy: ExistingTablePolicy = ExistingTablePolicy.SKIP
    apply_foreign_keys: bool = False
    apply_indexes: bool = True
    index_large_table_threshold: int = Field(default=1_000_000, ge=0)
    index_apply_overrides: dict[str, IndexApplyTiming] = Field(default_factory=dict)
    batch_size: int = Field(default=10_000, gt=0)
    large_row_batch_size: int | None = Field(default=None, gt=0)
    commit_interval: int = Field(default=10_000, gt=0)
    parallel_table_count: int = Field(default=1, gt=0)
    throttle_sleep_ms: int = Field(default=0, ge=0)
    checkpoint_resume: bool = True
    dry_run_report_path: str | None = None
    strict_source_only_columns: bool = False


class SafetyConfig(BaseModel):
    is_production_protection: bool = True
    allow_destructive_on_production: bool = False
    require_dry_run_before_destructive: bool = True


class ReportConfig(BaseModel):
    output_dir: str = "./reports"
    formats: list[str] = Field(default_factory=lambda: ["html", "json", "csv"])


class VerificationConfig(BaseModel):
    row_count: bool = True
    checksum_sample: bool = True
    checksum_sample_size: int = Field(default=100, gt=0)
    checksum_datetime_precision: str = "microseconds"
    checksum_timezone: str | None = None
    checksum_float_precision: int = Field(default=12, gt=0)
    pk_range_checksum: bool = False


class WatermarkConfig(BaseModel):
    column: str = Field(min_length=1)
    start_value: str | None = None
    end_value: str | None = None


class IncrementalConfig(BaseModel):
    enabled: bool = False
    watermarks: dict[str, WatermarkConfig] = Field(default_factory=dict)
    delete_sync: bool = False


class TableIncrementalConfig(BaseModel):
    watermark_column: str | None = Field(default=None, min_length=1)
    start_value: str | None = None
    end_value: str | None = None


class ColumnTransformConfig(BaseModel):
    source: str | None = Field(default=None, min_length=1)
    target_type: str | None = Field(default=None, min_length=1)
    default: Any | None = None
    null: bool = False
    skip: bool = False


class TableRunConfig(BaseModel):
    target_schema: str | None = Field(default=None, min_length=1)
    target_table: str | None = Field(default=None, min_length=1)
    comment: str | None = Field(default=None, min_length=1)
    columns: dict[str, ColumnTransformConfig] = Field(default_factory=dict)
    source_only_columns: dict[str, SourceOnlyColumnAction] = Field(default_factory=dict)
    incremental: TableIncrementalConfig = Field(default_factory=TableIncrementalConfig)


class AppConfig(BaseModel):
    job: JobConfig = Field(default_factory=JobConfig)
    source: SourceConfig = Field(default_factory=SourceConfig)
    target: TargetConfig = Field(default_factory=TargetConfig)
    migration: MigrationConfig = Field(default_factory=MigrationConfig)
    safety: SafetyConfig = Field(default_factory=SafetyConfig)
    report: ReportConfig = Field(default_factory=ReportConfig)
    verification: VerificationConfig = Field(default_factory=VerificationConfig)
    incremental: IncrementalConfig = Field(default_factory=IncrementalConfig)
    tables: dict[str, TableRunConfig] = Field(default_factory=dict)
