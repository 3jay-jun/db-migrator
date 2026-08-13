# Jigration

PostgreSQL 데이터를 MariaDB/MySQL로 안전하게 이관하기 위한 Python CLI 도구입니다.

v1 범위는 `PostgreSQL -> MariaDB/MySQL` full migration이며, dry-run, DDL 생성/실행, batch DML 이관, keyset checkpoint 기반 resume/retry, row count/checksum 검증, HTML/JSON/CSV 리포트를 제공합니다. v1.1 범위로 watermark 기반 증분 이관도 별도 명령으로 제공합니다.

## 빠른 시작

```powershell
uv sync --extra test
uv run db-migrator doctor
uv run db-migrator --help
uv run pytest
```

`uv`가 없다면 Python 3.11+ 가상환경을 만들고 프로젝트를 `test` extra와 함께 설치하세요.

## Windows GUI

PySide6 기반 네이티브 GUI도 제공합니다. GUI는 CLI와 같은 application service를 사용하므로 이관 로직은 중복 구현하지 않습니다.

```powershell
uv sync --extra gui --extra test
uv run jigration-gui
```

Windows onedir zip 빌드 방법은 `docs/gui.md`를 참고하세요.

## config.yml 작성

실제 실행은 루트의 `config.yml`을 읽습니다. 이 파일에는 DB 접속 정보가 들어가므로 커밋하지 마세요. 현재 `.gitignore`에서 `config.yml`, `config.local.yml`을 제외합니다.

가장 먼저 수정할 값은 아래 항목입니다.

```yaml
source:
  dbms: postgresql
  host: PostgreSQL_HOST
  port: 5432
  database: PostgreSQL_DATABASE_NAME
  schema: public
  user: PostgreSQL_READONLY_USER
  password: null

target:
  dbms: mysql
  host: MYSQL_HOST
  port: 3306
  database: MYSQL_DATABASE_NAME
  user: MYSQL_MIGRATION_USER
  password: null
  environment: staging
```

`source.database`는 PostgreSQL의 DB명이고, `source.schema`는 그 DB 안의 schema명입니다. DB명이 틀려도 schema scan 단계에서 실패하므로, 에러 메시지의 `database=... schema=... detail=...` 부분을 같이 확인하세요.

### SSH 터널로 private DB 접속

AWS EC2/VPC 내부 DB처럼 외부에서 직접 접근할 수 없는 DB는 `source.tunnel` 또는 `target.tunnel`을 사용합니다. `host`/`port`에는 프로그램이 붙을 로컬 터널 endpoint를 적고, `tunnel.remote_host`/`tunnel.remote_port`에는 SSH 서버에서 접근 가능한 실제 DB endpoint를 적습니다.

```yaml
source:
  dbms: postgresql
  host: 127.0.0.1
  port: 15433
  database: legacy
  schema: public
  user: readonly_user
  password: null
  tunnel:
    enabled: true
    ssh_host: ec2-public.example.com
    ssh_port: 22
    ssh_user: ec2-user
    auth_type: key
    private_key_path: C:\keys\service.pem
    private_key_passphrase_env: SSH_KEY_PASSPHRASE
    known_hosts_path: null
    remote_host: 127.0.0.1
    remote_port: 5432
    local_host: 127.0.0.1
    local_port: 15433
```

`auth_type: password`를 쓰면 `private_key_path` 대신 `ssh_password`를 설정합니다. `known_hosts_path`를 비워두면 기본값은 `{사용자}/.ssh/known_hosts`입니다. 처음 접속하는 EC2라면 먼저 `ssh-keyscan` 또는 일반 SSH 접속으로 host key를 등록한 뒤 실행하세요. `local_port`는 메인 DB 설정의 `port`와 맞춰 두는 것이 GUI에서 가장 직관적입니다. `local_port: 0`은 자동 포트 선택이지만 실행 전에는 메인 DB 포트에 표시할 수 없습니다.

## 권장 실행 순서

1. 로컬 환경 점검

```powershell
uv run db-migrator doctor
```

Python 버전, 필수 패키지, `reports/`, `checkpoints/`, `logs/` 쓰기 가능 여부, 선택 도구 `uv`, `docker`, `pyinstaller`를 확인합니다. DB에는 접속하지 않습니다.

2. dry-run 리포트 생성

```powershell
uv run db-migrator dry-run --config config.yml --output-dir reports/live-dry-run
```

PostgreSQL에 접속해 schema metadata를 읽고, target MySQL/MariaDB용 DDL과 위험 리포트를 생성합니다. target DB에는 쓰지 않습니다.

3. dry-run 결과 확인

확인 대상:
- `reports/live-dry-run/summary.html`
- `reports/live-dry-run/summary.json`
- `reports/live-dry-run/tables.csv`

type 변환 warning, generated column, 예약어/대소문자 identifier warning이 있으면 실제 DDL 실행 전 확인하세요.

4. target DDL 실행

```powershell
uv run db-migrator apply-ddl --config config.yml --output-file reports/live/ddl-execution.json
```

target DB에 `CREATE TABLE`을 실행합니다. `migration.existing_table_policy`가 `skip`이면 이미 존재하는 table은 건너뜁니다.
외래키까지 적용하려면 `migration.apply_foreign_keys: true`를 명시하세요. 기본값은 `false`라서 table 생성과 FK 적용을 분리해 검토할 수 있습니다.

5. 데이터 이관

```powershell
uv run db-migrator migrate-data --config config.yml --checkpoint-db checkpoints/live.sqlite
```

PostgreSQL에서 batch read하고, MySQL/MariaDB에 batch write합니다. PK/unique key가 있는 table은 keyset cursor를 사용해 마지막 성공 key를 SQLite checkpoint에 저장하고, PK/unique key가 없으면 offset resume으로 fallback합니다. offset fallback table은 dry-run/report에 `high risk: offset resume only` warning이 표시됩니다.

이미 같은 `job.name + table`의 완료 checkpoint가 있고 target row도 남아 있으면, 기본 `migrate-data`는 중복 삽입을 막기 위해 해당 table을 skip합니다. 같은 작업을 이어가려면 `resume` 또는 `retry-failed`를 사용하고, 처음부터 다시 맞추려면 `sync`, `overwrite`, 또는 새 `job.name`을 사용하세요.

6. 중단 또는 실패 후 재개

```powershell
uv run db-migrator resume --config config.yml --checkpoint-db checkpoints/live.sqlite
uv run db-migrator retry-failed --config config.yml --checkpoint-db checkpoints/live.sqlite
```

`resume`은 checkpoint 기준으로 이어서 실행합니다. `retry-failed`는 실패한 table만 다시 실행합니다. target commit 성공 후 checkpoint 저장이 실패한 batch는 `checkpoint_failed_after_commit` 상태로 기록되며, 중복 쓰기 위험이 있으므로 강제 재개 대신 검증 후 재실행 정책을 선택해야 합니다.

7. 이관 검증

```powershell
uv run db-migrator validate --config config.yml --output-dir reports/live-validation
```

source/target row count와 sample checksum을 비교하고 검증 리포트를 생성합니다. checksum sample은 PK/정렬 컬럼 기준 first N + last N을 비교합니다. PK가 있으면 PK 기준으로 source/target row를 매칭하고, PK가 없으면 위치 기반 비교로 fallback하므로 리포트 신뢰도 warning을 함께 확인하세요. 이 명령은 양쪽 DB에 조회 부하를 발생시킬 수 있습니다.

## 명령어 요약

| 명령 | DB 영향 | 용도 |
| --- | --- | --- |
| `doctor` | DB 접속 없음 | 로컬 실행 환경과 출력 디렉터리 점검 |
| `dry-run` | source 읽기, target 쓰기 없음 | schema scan, DDL/위험 리포트 생성 |
| `apply-ddl` | target DDL 실행 | MySQL/MariaDB table 생성 |
| `migrate-data` | source 읽기, target 쓰기 | full data batch migration |
| `resume` | source 읽기, target 쓰기 | checkpoint 기준 재개 |
| `retry-failed` | source 읽기, target 쓰기 | 실패 table만 재시도 |
| `validate` | source/target 읽기 | row count/checksum 검증 |
| `migrate-incremental` | source 읽기, target upsert | watermark 기반 증분 이관 |
| `self-test run` | Docker 컨테이너 실행, source/target 쓰기 | Docker 기반 end-to-end 이관 검증 |
| `package-check` | DB 접속 없음 | PyInstaller 사용 가능 여부 확인 |

각 명령의 옵션은 아래처럼 확인합니다.

```powershell
uv run db-migrator <command> --help
```

예:

```powershell
uv run db-migrator dry-run --help
uv run db-migrator apply-ddl --help
uv run db-migrator migrate-data --help
```

## 주요 설정값

| 경로 | 설명 | 권장 초기값 |
| --- | --- | --- |
| `job.name` | checkpoint/report 식별자 | 작업별 고정 이름 |
| `source.database` | PostgreSQL DB명 | 실제 source DB명 |
| `source.schema` | PostgreSQL schema명 | 보통 `public` |
| `target.database` | MySQL/MariaDB DB명 | 비어 있는 검증용 DB 권장 |
| `target.environment` | target 환경 | 첫 검증은 `staging` 또는 `local` |
| `migration.existing_table_policy` | 기존 table 처리 | 첫 검증은 `skip` |
| `migration.apply_foreign_keys` | CREATE TABLE 이후 FK 적용 여부 | 첫 검증은 `false` |
| `migration.batch_size` | source read batch 크기 | `10000` |
| `migration.commit_interval` | target commit 간격 | `10000` |
| `migration.parallel_table_count` | table 단위 병렬 이관 worker 수 | `1` |
| `migration.throttle_sleep_ms` | batch commit 후 대기 시간 | `0` |
| `migration.large_row_batch_size` | 대형 row table 전용 batch 크기 | 필요 시 지정 |
| `report.output_dir` | 리포트 출력 위치 | `./reports/live` |
| `verification.checksum_sample_size` | checksum sample row 수 | `100` |
| `verification.checksum_timezone` | timezone 있는 source datetime을 비교할 기준 timezone | 한국 서비스는 `Asia/Seoul` 권장 |
| `verification.pk_range_checksum` | PK range checksum 옵션 | `false` |

`existing_table_policy` 값:
- `skip`: 기존 table은 건너뜀
- `append`: 기존 table에 insert
- `sync`: source 기준으로 target row를 upsert/delete
- `overwrite`: 기존 target table을 drop 후 다시 생성

운영 환경에서 destructive 정책을 사용할 때는 `safety` 설정이 차단할 수 있습니다. `sync`, `overwrite`는 target 데이터를 삭제하거나 덮어쓸 수 있으므로 처음 검증은 `skip`으로 시작하세요. `overwrite` DDL 실행 기록은 리포트 디렉터리의 `overwrite-audit.sqlite`에 남습니다.

## 운영 기준 동기화

target 데이터를 source 운영 기준으로 맞춰야 하면 `migration.existing_table_policy: sync`를 사용합니다.

```yaml
migration:
  existing_table_policy: sync
  parallel_table_count: 1
  throttle_sleep_ms: 0
```

`sync` 정책은 source에 있는 row는 target에 upsert하고, source에 없는 target row는 삭제합니다. 즉 source가 SSOT이며, target의 불일치 데이터는 source 기준으로 정정됩니다.

주의사항:
- `sync`는 PK 또는 unique key가 있는 table에서만 실행됩니다. key가 없으면 row identity를 안정적으로 판단할 수 없어 실패 처리합니다.
- target 삭제가 포함되는 destructive 정책입니다. 운영 target에서는 dry-run 리포트와 safety 설정을 먼저 확인하세요.
- table 내부 batch는 병렬화하지 않습니다. `parallel_table_count`는 table 단위 병렬 처리만 수행해 checkpoint 순서를 단순하게 유지합니다.
- `throttle_sleep_ms`가 0보다 크면 batch commit 후 지정 시간만큼 대기해 source/target 부하를 낮춥니다.

## 증분 이관

증분 이관은 full migration과 별도 명령입니다.

```yaml
incremental:
  enabled: true
  delete_sync: false
  watermarks:
    users:
      column: updated_at
      start_value: "2026-01-01T00:00:00"
      end_value: "2026-02-01T00:00:00"
```

GUI에서는 테이블별 target명과 watermark 설정을 `tables` 섹션에 저장합니다. source 읽기는 원본 테이블을 사용하고, target DDL/DML/검증 리포트는 매핑된 target 테이블명을 사용합니다.

```yaml
tables:
  public.users:
    target_table: app_users
    incremental:
      watermark_column: updated_at
      start_value: "2026-01-01T00:00:00"
      end_value: "2026-02-01T00:00:00"
```

실행:

```powershell
uv run db-migrator migrate-incremental --config config.yml --output-dir reports/live-incremental
```

현재 DELETE sync는 자동 실행하지 않고 리포트에 수동 후속 작업으로 남깁니다.

## Docker Self-Test

Docker 기반 self-test는 PostgreSQL source와 MariaDB target 컨테이너를 띄운 뒤 `dry-run -> apply-ddl -> migrate-data -> validate`를 end-to-end로 실행합니다.

```powershell
uv run db-migrator self-test run
```

실행 중에는 stage 로그와 batch commit 로그가 실시간으로 출력됩니다. 대용량 table은 `batch_committed` 로그의 `progress`, `rows/sec`, `eta`, `cursor`, `next_offset`으로 실제 이관 진행 여부를 확인하세요.

예:

```text
INFO batch_committed table=bulk_events Batch committed for bulk_events. progress=50000/1000000 (5.0%) batch=10 rows/sec=12000 eta=1m19s cursor=keyset next_offset=50000
INFO checkpoint_saved table=bulk_events Checkpoint saved for bulk_events. checkpoint_batch=10 cursor=keyset next_offset=50000
```

대형 table row 수는 `--large-rows`로 조절합니다. 기본값은 `100000`입니다.

```powershell
uv run db-migrator self-test run --large-rows 1000000
```

실패 원인을 컨테이너 안에서 확인하려면 cleanup을 막습니다.

```powershell
uv run db-migrator self-test run --large-rows 10000 --keep-containers
```

기본 scenario는 `src/db_migrator/selftest/scenarios/pg_to_mariadb`입니다. self-test runner는 DBMS별 seed 실행 방식을 코드에 하드코딩하지 않고, scenario의 `selftest.yml`에서 외부 주입받습니다.

scenario 기본 구조:

```text
src/db_migrator/selftest/
  docker-compose.yml
  scenarios/<scenario-name>/
    selftest.yml
    source/<source-dbms>/schema.sql
    source/<source-dbms>/seed.sql
```

`docker-compose.yml`은 `source`, `target` 두 서비스만 관리합니다. 사람이 scenario별로 작성하는 파일은 `selftest.yml` 하나이며, 이 파일에 Docker image/port/healthcheck/container env, source seed 명령, migration 옵션을 함께 둡니다. runner는 이 값을 읽어 임시 Docker env 파일과 migration config를 생성합니다.

```yaml
compose_file: ../../docker-compose.yml
docker:
  source:
    dbms: postgresql
    image: postgres:16
    host_port: 15432
    container_port: 5432
    database: source
    schema: public
    user: source_user
    password: source_pass
    healthcheck: pg_isready -U source_user -d source
    container_environment:
      POSTGRES_DB: source
      POSTGRES_USER: source_user
      POSTGRES_PASSWORD: source_pass
  target:
    dbms: mariadb
    image: mariadb:11
    host_port: 13306
    container_port: 3306
    database: target
    user: target_user
    password: target_pass
    root_password: root_pass
    environment: local
    healthcheck: mariadb-admin ping -h 127.0.0.1 -u root -proot_pass --silent
    container_environment:
      MARIADB_DATABASE: target
      MARIADB_USER: target_user
      MARIADB_PASSWORD: target_pass
      MARIADB_ROOT_PASSWORD: root_pass
source_seed:
  service: source
  schema_file: source/postgresql/schema.sql
  seed_file: source/postgresql/seed.sql
  schema_command: [...]
  seed_command: [...]
migration_config:
  job:
    name: self-test-pg-to-mariadb
  migration:
    existing_table_policy: skip
    batch_size: 5000
```

새 DBMS 조합을 추가할 때는 runner 코드를 수정하지 말고 새 scenario 폴더에 `selftest.yml`과 SQL seed만 추가하고 `--scenario`로 선택하세요. Docker image가 요구하는 env 이름도 해당 scenario의 `container_environment`에 직접 둡니다. 중앙 `docker-compose.yml`은 `source`, `target` 두 서비스만 유지합니다. `--compose-file`은 중앙 compose 파일을 임시로 교체해야 할 때만 사용하는 고급 override입니다.

현재 기본 scenario 검증 범위:
- PK/FK/composite PK/unique key가 있는 업무성 table
- PK가 없는 audit table의 offset fallback warning
- `integer`, `bigint`, `numeric`, `boolean`, `date`, `timestamp`, `timestamptz`, `text`, `jsonb`, `bytea`, `uuid`
- `large_row_batch_size`가 적용되는 TEXT/JSON/BYTEA 포함 table
- `--large-rows`로 조절되는 대용량 `bulk_events` table

역방향 MariaDB -> PostgreSQL self-test:

```powershell
uv run db-migrator self-test run --scenario mariadb_to_pg --large-rows 1000
```

대용량 검증:

```powershell
uv run db-migrator self-test run --scenario mariadb_to_pg --large-rows 100000
uv run db-migrator self-test run --scenario mariadb_to_pg --large-rows 1000000
```

`mariadb_to_pg` scenario는 `source` 컨테이너의 MariaDB 데이터를 `target` 컨테이너의 PostgreSQL로 이관합니다. 사용 포트는 `source=23306`, `target=25432`입니다. 실패 원인 확인이 필요하면 `--keep-containers`를 붙여 컨테이너를 남긴 뒤 DB에 직접 접속해 확인하세요.

Docker Desktop이 설치되어 있지 않거나 실행 중이 아니면 `Docker is not installed or not running. Self-test requires Docker Desktop.` 메시지로 중단됩니다.

## 자주 보는 에러

`PostgreSQL connection failed before schema scan`

`source.host`, `source.port`, `source.database`, `source.user`, `source.password`를 확인하세요. DB명이 틀리면 이 단계에서 실패합니다.

`PostgreSQL schema metadata query failed`

DB 접속은 성공했지만 `source.schema`가 없거나 metadata 조회 권한이 부족한 경우입니다.

`Config file failed validation`

`config.yml`의 YAML 문법 또는 허용되지 않는 enum 값을 확인하세요. 허용 값은 `src/db_migrator/config/models.py`가 기준입니다.

## 참고 문서

- 에이전트 진입점: `AGENTS.md`
- 상세 작업 규칙: `Codex.md`
- 명령 요약: `docs/generated/cli-reference.md`
- 샘플 설정: `docs/product-specs/sample-configs.md`
- 전체 구현 계획: `05-plan.md`
