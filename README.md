# Jigration

PostgreSQL 데이터를 MariaDB/MySQL로 안전하게 이관하기 위한 Python 기반 DB 마이그레이션 도구입니다.

Jigration은 단순 데이터 복사가 아니라 schema scan, dry-run 리포트, DDL 적용, batch DML 이관, checkpoint 기반 resume/retry, row count/checksum 검증, 실행 리포트를 하나의 운영 흐름으로 제공합니다. 현재 구현은 PostgreSQL, MySQL, MariaDB adapter를 포함하며 기본 사용 흐름은 PostgreSQL source에서 MySQL/MariaDB target으로의 이관입니다.

## 주요 기능

- Source DB schema/table/column/PK/index/FK metadata scan
- Common schema model 기반 DBMS별 DDL 생성
- dry-run HTML/JSON/CSV 리포트와 manual review/warning 분류
- target DDL, index, foreign key 적용 명령 분리
- streaming read와 batch write 기반 대용량 DML 이관
- SQLite checkpoint 기반 resume/retry
- row count와 checksum sample 검증
- watermark 기반 v1.1 증분 이관 명령
- production destructive 작업을 막는 Safety Guard
- Docker 기반 PostgreSQL/MariaDB self-test scenario
- PySide6 기반 Windows-first GUI
- SSH tunnel 기반 private DB 접속 설정

## 기술 스택

| 영역 | 사용 기술 |
| --- | --- |
| Language | Python 3.11+ |
| CLI | Typer, Rich |
| GUI | PySide6 optional extra |
| Config | Pydantic v2, PyYAML |
| DB Driver | psycopg, PyMySQL |
| SSH Tunnel | paramiko, sshtunnel |
| Checkpoint | SQLite |
| Test/Packaging | pytest, PyInstaller |
| Project Tooling | uv, setuptools |

## 프로젝트 구조

```text
src/db_migrator/
  adapters/      # DBMS별 source/target adapter와 provider registry
  application/   # CLI/GUI가 공유하는 application service
  cli/           # Typer CLI entrypoint
  config/        # pydantic config model과 loader
  core/          # migration, checkpoint, validation, safety 도메인 로직
  gui/           # PySide6 GUI
  reports/       # dry-run/final/incremental report writer
  schema/        # common schema model, type mapping, dialect helpers
  selftest/      # Docker self-test runner와 scenarios
tests/unit/      # unit tests
docs/generated/  # generated CLI reference
docs/product-specs/
```

## 시작하기

### 사전 요구사항

- Python 3.11 이상
- `uv` 권장
- 실제 DB 이관 시 PostgreSQL source와 MySQL/MariaDB target 접속 정보
- GUI 실행 시 `gui` extra
- Docker self-test 실행 시 Docker Desktop

### 설치

개발/테스트 환경:

```powershell
uv sync --extra test
```

GUI까지 설치:

```powershell
uv sync --extra gui --extra test
```

`uv`를 쓰지 않는 경우 Python 3.11+ 가상환경에서 editable install을 사용합니다.

```powershell
python -m pip install -e ".[test]"
python -m pip install -e ".[gui,test]"
```

### 기본 확인

```powershell
uv run db-migrator --help
uv run db-migrator doctor
uv run pytest
```

`doctor`는 Python 버전, 필수 package import, `reports/`, `checkpoints/`, `logs/` 쓰기 가능 여부, 선택 도구 `uv`, `docker`, `pyinstaller`를 확인합니다. DB에는 접속하지 않습니다.

## 설정

실제 실행은 루트의 `config.yml`을 읽습니다. DB 접속 정보가 들어가므로 커밋하지 마세요. `.gitignore`는 `config.yml`, `config.local.yml`, `config.dev.yml`, `config.stg.yml`, `config.prd.yml`을 제외합니다.

최소 설정 예:

```yaml
job:
  name: legacy-postgres-to-mysql
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

`source.database`는 PostgreSQL DB명이고 `source.schema`는 해당 DB 안의 schema명입니다. DB명이나 schema명이 틀리면 schema scan 단계에서 실패합니다.

더 많은 예시는 `docs/product-specs/sample-configs.md`를 참고하세요.

## SSH 터널

외부에서 직접 접근할 수 없는 private DB는 `source.tunnel` 또는 `target.tunnel`을 사용합니다. `host`/`port`에는 프로그램이 접속할 로컬 endpoint를 적고, `tunnel.remote_host`/`tunnel.remote_port`에는 SSH 서버에서 접근 가능한 실제 DB endpoint를 적습니다.

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

`auth_type: password`를 쓰면 `private_key_path` 대신 `ssh_password`를 설정합니다. `known_hosts_path`를 비워두면 기본값은 `{사용자}/.ssh/known_hosts`입니다. 처음 접속하는 EC2라면 먼저 일반 SSH 접속 또는 `ssh-keyscan`으로 host key를 등록하세요.

## 권장 실행 순서

1. 로컬 환경 점검

```powershell
uv run db-migrator doctor
```

2. dry-run 리포트 생성

```powershell
uv run db-migrator dry-run --config config.yml --output-dir reports/live-dry-run
```

source metadata를 읽고 target DDL과 위험 리포트를 생성합니다. target DB에는 쓰지 않습니다. `summary.html`, `summary.json`, `tables.csv`에서 type 변환 warning, generated column, 예약어/대소문자 identifier warning을 확인하세요.

3. target DDL 실행

```powershell
uv run db-migrator apply-ddl --config config.yml --output-file reports/live/ddl-execution.json
```

target DB에 `CREATE TABLE`을 실행합니다. `migration.existing_table_policy: skip`이면 이미 존재하는 table은 DDL만 건너뛰고 데이터 이관 대상에는 포함합니다.

4. index 또는 foreign key 적용

```powershell
uv run db-migrator apply-indexes --config config.yml --output-file reports/live/index-execution.json
```

외래키까지 적용하려면 `migration.apply_foreign_keys: true`를 명시합니다. 기본값은 `false`라서 table 생성과 FK 적용을 분리해 검토할 수 있습니다.

5. 데이터 이관

```powershell
uv run db-migrator migrate-data --config config.yml --checkpoint-db checkpoints/live.sqlite
```

PK/unique key가 있는 table은 keyset cursor를 사용해 마지막 성공 key를 SQLite checkpoint에 저장합니다. PK/unique key가 없으면 offset resume으로 fallback하며 dry-run/report에 high-risk warning이 표시됩니다.

6. 중단 또는 실패 후 재개

```powershell
uv run db-migrator resume --config config.yml --checkpoint-db checkpoints/live.sqlite
uv run db-migrator retry-failed --config config.yml --checkpoint-db checkpoints/live.sqlite
```

`resume`은 checkpoint 기준으로 이어서 실행합니다. `retry-failed`는 실패 table만 다시 실행합니다. target commit 성공 후 checkpoint 저장이 실패한 batch는 `checkpoint_failed_after_commit` 상태로 기록되며, 중복 쓰기 위험이 있으므로 검증 후 재실행 정책을 선택해야 합니다.

7. 이관 검증

```powershell
uv run db-migrator validate --config config.yml --output-dir reports/live-validation
```

source/target row count와 checksum sample을 비교하고 검증 리포트를 생성합니다. 이 명령은 양쪽 DB에 조회 부하를 발생시킬 수 있습니다.

## CLI 명령

| 명령 | DB 영향 | 용도 |
| --- | --- | --- |
| `bootstrap` | DB 접속 없음 | 기본 설정/실행 준비 이벤트 생성 |
| `doctor` | DB 접속 없음 | 로컬 실행 환경과 출력 디렉터리 점검 |
| `dry-run` | source 읽기, target 쓰기 없음 | schema scan, DDL/위험 리포트 생성 |
| `apply-ddl` | target DDL 실행 | target table 생성 |
| `apply-indexes` | target DDL 실행 | index 적용 |
| `migrate-data` | source 읽기, target 쓰기 | full data batch migration |
| `resume` | source 읽기, target 쓰기 | checkpoint 기준 재개 |
| `retry-failed` | source 읽기, target 쓰기 | 실패 table만 재시도 |
| `validate` | source/target 읽기 | row count/checksum 검증 |
| `migrate-incremental` | source 읽기, target upsert | watermark 기반 증분 이관 |
| `self-test run` | Docker 컨테이너 실행, source/target 쓰기 | Docker 기반 end-to-end 이관 검증 |
| `package-check` | DB 접속 없음 | PyInstaller 사용 가능 여부 확인 |

각 명령 옵션은 아래처럼 확인합니다.

```powershell
uv run db-migrator <command> --help
```

명령 요약 문서는 `docs/generated/cli-reference.md`에도 있습니다.

## 주요 설정값

| 경로 | 설명 | 권장 초기값 |
| --- | --- | --- |
| `job.name` | checkpoint/report 식별자 | 작업별 고정 이름 |
| `source.dbms` | source DBMS | `postgresql` |
| `source.database` | source DB명 | 실제 source DB명 |
| `source.schema` | source schema명 | PostgreSQL은 보통 `public` |
| `target.dbms` | target DBMS | `mysql` 또는 `mariadb` |
| `target.database` | target DB명 | 비어 있는 검증용 DB 권장 |
| `target.environment` | target 환경 | 첫 검증은 `staging` 또는 `local` |
| `migration.existing_table_policy` | 기존 table 처리 | 첫 검증은 `skip` |
| `migration.apply_foreign_keys` | CREATE TABLE 이후 FK 적용 여부 | 첫 검증은 `false` |
| `migration.apply_indexes` | index 적용 여부 | `true` |
| `migration.batch_size` | source read batch 크기 | `10000` |
| `migration.commit_interval` | target commit 간격 | `10000` |
| `migration.parallel_table_count` | table 단위 병렬 이관 worker 수 | `1` |
| `migration.throttle_sleep_ms` | batch commit 후 대기 시간 | `0` |
| `migration.large_row_batch_size` | 대형 row table 전용 batch 크기 | 필요 시 지정 |
| `report.output_dir` | 리포트 출력 위치 | `./reports/live` |
| `verification.checksum_sample_size` | checksum sample row 수 | `100` |
| `verification.checksum_timezone` | timezone 있는 datetime 비교 기준 | 한국 서비스는 `Asia/Seoul` 권장 |
| `verification.pk_range_checksum` | PK range checksum 옵션 | `false` |

`existing_table_policy` 값:

- `skip`: 기존 table은 DDL만 건너뛰고 DML은 실행
- `compare_only`: schema 비교 리포트만 생성
- `append`: source에는 있지만 target에는 없는 table만 생성/적재
- `sync`: source 선택 범위를 기준으로 target table/data를 맞춤
- `truncate_reload`: target table truncate 후 재적재
- `overwrite`: source/target 공통 table을 drop 후 재생성

`sync`, `truncate_reload`, `overwrite`는 target 데이터를 삭제하거나 덮어쓸 수 있는 destructive 정책입니다. 운영 target에서는 dry-run 리포트와 Safety Guard 설정을 먼저 확인하세요.

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

GUI에서는 테이블별 target명과 watermark 설정을 `tables` 섹션에 저장합니다.

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

## Windows GUI

CLI와 같은 application service를 사용하는 PySide6 기반 GUI를 제공합니다.

```powershell
uv sync --extra gui --extra test
uv run jigration-gui
```

Windows onedir zip 빌드:

```powershell
uv sync --extra gui --extra test
.\scripts\build-gui.ps1
Compress-Archive -Path dist\Jigration -DestinationPath dist\Jigration.zip -Force
```

GUI packaging 세부사항은 `docs/gui.md`를 참고하세요.

## Docker Self-Test

Docker 기반 self-test는 scenario에 정의된 source/target 컨테이너를 띄운 뒤 `dry-run -> apply-ddl -> migrate-data -> validate`를 end-to-end로 실행합니다.

```powershell
uv run db-migrator self-test run
uv run db-migrator self-test run --large-rows 1000000
uv run db-migrator self-test run --scenario mariadb_to_pg --large-rows 1000
```

기본 scenario는 `src/db_migrator/selftest/scenarios/pg_to_mariadb`입니다. 역방향 검증용 `mariadb_to_pg` scenario도 포함되어 있습니다. Docker Desktop이 설치되어 있지 않거나 실행 중이 아니면 `Docker is not installed or not running. Self-test requires Docker Desktop.` 메시지로 중단됩니다.

## 테스트와 품질 확인

```powershell
uv run pytest
uv run db-migrator package-check
```

테스트 설정은 `pyproject.toml`의 `tool.pytest.ini_options`를 따릅니다. pytest 임시 파일과 cache는 `.tmp/` 아래에 생성됩니다.

## 자주 보는 에러

`PostgreSQL connection failed before schema scan`

`source.host`, `source.port`, `source.database`, `source.user`, `source.password`를 확인하세요. DB명이 틀리면 이 단계에서 실패합니다.

`PostgreSQL schema metadata query failed`

DB 접속은 성공했지만 `source.schema`가 없거나 metadata 조회 권한이 부족한 경우입니다.

`Config file failed validation`

`config.yml`의 YAML 문법 또는 허용되지 않는 enum 값을 확인하세요. 허용 값은 `src/db_migrator/config/models.py`가 기준입니다.

## 기여 방법

이 저장소는 phase 기반 작업 문서를 사용합니다.

- 작업 전 `AGENTS.md`, `Codex.md`, `02-prd.md`, `03-Preplan.md`, `05-plan.md`를 확인하세요.
- 새 phase 작업은 `docs/exec-plans/active/`에 세부 체크리스트를 둡니다.
- 기존 도메인 규칙은 `rg`로 먼저 검색하고 SSOT를 확장하세요.
- DBMS별 SQL 방언은 adapter와 schema dialect helper에 격리하세요.
- password, token, API key, connection secret은 README, fixture, log, report에 남기지 마세요.

## 라이선스

현재 저장소에는 별도 `LICENSE` 파일이 없습니다.

## 참고 문서

- 에이전트 진입점: `AGENTS.md`
- 상세 작업 규칙: `Codex.md`
- 제품 요구사항: `02-prd.md`
- 아키텍처/도메인 모델: `03-Preplan.md`
- 구현 계획: `05-plan.md`
- CLI reference: `docs/generated/cli-reference.md`
- sample config: `docs/product-specs/sample-configs.md`
- GUI packaging: `docs/gui.md`
