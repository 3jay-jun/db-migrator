# DB Migrator

PostgreSQL 데이터를 MariaDB/MySQL로 안전하게 이관하기 위한 Python CLI 도구입니다.

v1 범위는 `PostgreSQL -> MariaDB/MySQL` full migration이며, dry-run, DDL 생성/실행, batch DML 이관, checkpoint 기반 resume/retry, row count/checksum 검증, HTML/JSON/CSV 리포트를 제공합니다. v1.1 범위로 watermark 기반 증분 이관도 별도 명령으로 제공합니다.

## 빠른 시작

```powershell
uv sync --extra test
uv run db-migrator doctor
uv run db-migrator --help
uv run pytest
```

`uv`가 없다면 Python 3.11+ 가상환경을 만들고 프로젝트를 `test` extra와 함께 설치하세요.

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

5. 데이터 이관

```powershell
uv run db-migrator migrate-data --config config.yml --checkpoint-db checkpoints/live.sqlite
```

PostgreSQL에서 server-side cursor로 batch read하고, MySQL/MariaDB에 batch insert합니다. 성공 batch 위치는 SQLite checkpoint에 저장됩니다.

6. 중단 또는 실패 후 재개

```powershell
uv run db-migrator resume --config config.yml --checkpoint-db checkpoints/live.sqlite
uv run db-migrator retry-failed --config config.yml --checkpoint-db checkpoints/live.sqlite
```

`resume`은 checkpoint 기준으로 이어서 실행합니다. `retry-failed`는 실패한 table만 다시 실행합니다.

7. 이관 검증

```powershell
uv run db-migrator validate --config config.yml --output-dir reports/live-validation
```

source/target row count와 sample checksum을 비교하고 검증 리포트를 생성합니다. 이 명령은 양쪽 DB에 조회 부하를 발생시킬 수 있습니다.

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
| `self-test run` | Docker 사전 점검 | Docker 기반 self-test 준비 확인 |
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
| `migration.batch_size` | source read batch 크기 | `10000` |
| `migration.commit_interval` | target commit 간격 | `10000` |
| `report.output_dir` | 리포트 출력 위치 | `./reports/live` |
| `verification.checksum_sample_size` | checksum sample row 수 | `100` |
| `verification.checksum_timezone` | timezone 있는 source datetime을 비교할 기준 timezone | 한국 서비스는 `Asia/Seoul` 권장 |

`existing_table_policy` 값:
- `skip`: 기존 table은 건너뜀
- `compare_only`: 비교만 수행
- `append`: 기존 table에 insert
- `truncate_reload`: truncate 후 재적재
- `overwrite`: overwrite 계열 작업

운영 환경에서 destructive 정책을 사용할 때는 `safety` 설정이 차단할 수 있습니다. 처음 검증은 `skip`으로 시작하세요.

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

실행:

```powershell
uv run db-migrator migrate-incremental --config config.yml --output-dir reports/live-incremental
```

현재 DELETE sync는 자동 실행하지 않고 리포트에 수동 후속 작업으로 남깁니다.

## 자주 보는 에러

`PostgreSQL connection failed before schema scan`

`source.host`, `source.port`, `source.database`, `source.user`, `source.password`를 확인하세요. DB명이 틀리면 이 단계에서 실패합니다.

`PostgreSQL schema metadata query failed`

DB 접속은 성공했지만 `source.schema`가 없거나 metadata 조회 권한이 부족한 경우입니다.

`Config file failed validation`

`config.yml`의 YAML 문법 또는 허용되지 않는 enum 값을 확인하세요. 허용 값은 `src/db_migrator/config/models.py`가 기준입니다.

## 참고 문서

- 명령 요약: `docs/generated/cli-reference.md`
- 샘플 설정: `docs/product-specs/sample-configs.md`
- 전체 구현 계획: `05-plan.md`
