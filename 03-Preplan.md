# DB 마이그레이션 도구 preplan

## 1. 프로젝트 목적

이 프로젝트는 서로 다른 DBMS 간 테이블 구조와 데이터를 안전하게 이관하기 위한 Python 기반 DB 마이그레이션 도구를 구현한다.

v1은 실무 적용 가능성과 구현 난이도를 고려해 `PostgreSQL -> MariaDB/MySQL` 이관을 우선 지원한다. 단, 구조는 특정 DBMS 조합에 종속되지 않도록 `Source Adapter -> Common Schema Model -> Target Adapter` 흐름으로 설계한다.

핵심 목표는 단순 데이터 복사가 아니라 다음 작업을 하나의 실행 흐름으로 제공하는 것이다.

- Source DB 접속 및 스키마 스캔
- Target DB 테이블 생성 DDL 생성/실행
- 대용량 데이터 batch 이관
- dry-run 리포트
- checkpoint 기반 중단/재시도
- row count 및 checksum sample 검증
- 실시간 로그/진행률 이벤트 발행
- HTML/JSON/CSV 리포트 생성
- 운영 DB 파괴 작업 방지

## 2. v1 범위

### 2.1 지원 범위

- Source DB: PostgreSQL
- Target DB: MariaDB/MySQL
- 실행 형태: Python CLI 우선
- 패키징: Windows 실행파일 배포 가능 구조
- 이관 대상:
  - table
  - column
  - primary key
  - 기본 index
  - data
- 선택 지원:
  - foreign key metadata scan
  - foreign key 후처리 생성
  - Docker 기반 self-test

### 2.2 제외 범위

다음 항목은 v1 자동 변환 대상에서 제외한다. 감지 가능한 범위에서는 리포트에 `unsupported` 또는 `manual_review`로 기록한다.

- view 변환
- function 변환
- procedure 변환
- trigger 변환
- 복잡한 SQL 자동 변환
- 완전 무중단 CDC
- delete 자동 동기화
- 양방향 동기화
- 애플리케이션 코드 자동 수정

## 3. 기술 스택

### 3.1 추천 스택

- Language: Python 3.11+
- Project manager: `uv`
- CLI: `typer`
- Console/log UI: `rich`
- Config validation: `pydantic`, `pydantic-settings`
- PostgreSQL driver: `psycopg`
- MariaDB/MySQL driver: `pymysql` 기본, 성능 필요 시 `mysqlclient` 선택 지원
- Checkpoint DB: SQLite
- Report:
  - HTML summary
  - JSON detail
  - CSV table report
- Test:
  - `pytest`
  - optional Docker Compose integration test
- Packaging:
  - `PyInstaller`

### 3.2 대안 비교

프로젝트 관리:

| 대안 | 장점 | 단점 | 추천 |
| --- | --- | --- | --- |
| uv | 빠르고 현대적인 Python 프로젝트 관리에 적합 | 일부 팀에는 아직 익숙하지 않을 수 있음 | 추천 |
| poetry | 안정적이고 사용 경험이 많음 | uv보다 느리고 설정이 무거울 수 있음 | 가능 |
| requirements.txt | 단순함 | lock/reproducibility가 약함 | 비추천 |

CLI:

| 대안 | 장점 | 단점 | 추천 |
| --- | --- | --- | --- |
| typer | 명령 구조, 타입 힌트, 도움말 생성이 좋음 | argparse보다 의존성 추가 | 추천 |
| argparse | 표준 라이브러리라 의존성 없음 | 복잡한 CLI에서 코드가 장황해짐 | 가능 |

MySQL/MariaDB driver:

| 대안 | 장점 | 단점 | 추천 |
| --- | --- | --- | --- |
| pymysql | 순수 Python이라 설치/패키징이 쉬움 | massive batch insert, large object 처리에서 병목 가능 | v1 기본 |
| mysqlclient | C extension 기반이라 대량 insert 성능이 좋음 | Windows 패키징과 빌드 의존성이 번거로울 수 있음 | 성능 옵션 |

패키징:

| 대안 | 장점 | 단점 | 추천 |
| --- | --- | --- | --- |
| PyInstaller | Windows exe 배포에 가장 현실적 | 바이너리 크기가 커질 수 있음 | 추천 |
| Nuitka | 성능과 배포 안정성이 좋을 수 있음 | 설정 난이도가 높음 | 후보 |

## 4. 핵심 설계 원칙

### 4.1 Core Engine과 CLI UI 분리

마이그레이션 실행 로직과 CLI 입출력 로직은 철저히 분리한다.

Core Engine은 다음을 알면 안 된다.

- typer
- rich
- CLI prompt
- CLI progress bar
- 터미널 출력 형식
- FastAPI/WebSocket 구현 방식

Core Engine은 마이그레이션 상태를 `EventPublisher` 인터페이스로만 발행한다. v1부터 기본 구현은 `QueueEventPublisher` 하나로 통일하고, CLI 출력과 추후 FastAPI/WebSocket 확장은 같은 queue consumer를 교체하거나 추가하는 방식으로 처리한다.

```text
CLI UI
  -> config 로드
  -> password prompt
  -> Core Engine 호출
  -> Engine Event 구독
  -> rich console 출력

Core Engine
  -> schema scan
  -> plan 생성
  -> DDL/DML 실행
  -> checkpoint 저장
  -> validation 실행
  -> report data 생성
  -> event 발행

Future FastAPI UI
  -> Core Engine 호출
  -> Engine Event 구독
  -> WebSocket으로 상태 전송
```

### 4.2 DBMS 방언은 Adapter에 격리

Core Engine은 SQL 문자열을 직접 조립하지 않는다. DBMS별 문법 차이는 Adapter 내부에만 존재해야 한다.

Core Engine이 모르는 것:

- PostgreSQL `pg_catalog`
- MySQL `information_schema`
- `ON DUPLICATE KEY UPDATE`
- `ON CONFLICT`
- identifier quoting 방식
- `SERIAL`, `AUTO_INCREMENT`
- `jsonb`, `timestamp with time zone`
- DBMS별 truncate/upsert 문법

Core Engine은 다음과 같은 공통 인터페이스만 호출한다.

```python
source_adapter.scan_schema(...)
source_adapter.read_rows(...)
target_adapter.generate_create_table(...)
target_adapter.write_batch(...)
# v1.1 incremental extension
target_adapter.upsert_batch(...)
target_adapter.validate_row_count(...)
```

하드 제약:

- Source DB의 schema/data를 Target DB로 직접 변환하는 로직은 엄격히 금지한다.
- 모든 schema 변환은 반드시 `Source DB Metadata -> Common Schema Model -> Target DDL` 순서로 수행한다.
- 모든 data type 변환은 반드시 `Source Value/Type -> Standard Value/Type -> Target Value/Type` 순서로 수행한다.
- `postgres.py` 안에서 MySQL DDL/DML을 생성하거나, `mysql.py` 안에서 PostgreSQL metadata 구조에 직접 의존하는 코드는 금지한다.
- 이 제약은 테스트로 보호한다. 예를 들어 Adapter 단위 테스트에서 direct source-to-target 변환 함수가 생기지 않도록 타입 매핑 책임을 `schema/type_mapping.py`에 둔다.

### 4.3 과도한 파일 분리 금지

v1에서는 DBMS가 `PostgreSQL`, `MariaDB/MySQL`뿐이므로 Adapter 파일을 과도하게 쪼개지 않는다.

초기 구조:

```text
adapters/base.py
adapters/postgres.py
adapters/mysql.py
```

나중에 Oracle, MSSQL, Tibero 등이 추가되거나 파일 책임이 커지는 시점에만 다음처럼 확장한다.

```text
adapters/postgres/
  schema_reader.py
  data_reader.py
  type_normalizer.py

adapters/mysql/
  ddl_generator.py
  data_writer.py
  validator.py
```

### 4.4 타입 변환 흐름

Source 타입을 Target 타입으로 바로 변환하지 않는다.

```text
Source DB Type
  -> CommonType
  -> Target DB Type
```

예:

```text
PostgreSQL varchar(100)
  -> CommonType.STRING(length=100)
  -> MySQL varchar(100)

PostgreSQL jsonb
  -> CommonType.JSON(warning=jsonb_semantic_difference)
  -> MySQL json 또는 longtext
```

타입 변환 정책은 다음 4단계로 분류한다.

| 정책 | 설명 |
| --- | --- |
| AUTO_CONVERT | 명확한 1:1 매핑 |
| WARN_CONVERT | 변환은 가능하지만 의미 차이 또는 손실 가능성 경고 |
| MANUAL_REVIEW | 자동 변환 신뢰도가 낮아 사용자 확인 필요 |
| UNSUPPORTED | v1 자동 처리 제외 |

## 5. 프로젝트 구조

```text
src/db_migrator/
  __init__.py

  core/
    engine.py
    events.py
    workflow.py
    migration_plan.py
    checkpoint.py
    validation.py
    safety_guard.py
    report_data.py

  schema/
    models.py
    common_types.py
    type_mapping.py
    dependency.py

  adapters/
    base.py
    postgres.py
    mysql.py

  config/
    models.py
    loader.py
    defaults.py

  reports/
    writer.py
    html_writer.py
    json_writer.py
    csv_writer.py
    templates/

  cli/
    main.py
    commands.py
    prompts.py
    console_events.py

  selftest/
    docker_compose.yml
    sample_source.sql
    sample_config.yml
    runner.py

tests/
  unit/
  integration/

reports/
checkpoints/
logs/
```

## 6. 주요 도메인 모델

```python
@dataclass(frozen=True)
class TableRef:
    schema: str
    name: str


@dataclass(frozen=True)
class ColumnSchema:
    name: str
    source_type: str
    common_type: CommonType
    nullable: bool
    default: str | None
    is_generated: bool
    generation_expression: str | None
    ordinal_position: int
    warnings: list[SchemaWarning]


@dataclass(frozen=True)
class TableSchema:
    ref: TableRef
    columns: list[ColumnSchema]
    primary_key: PrimaryKey | None
    indexes: list[IndexSchema]
    foreign_keys: list[ForeignKeySchema]
    estimated_rows: int | None


@dataclass(frozen=True)
class MigrationOptions:
    mode: MigrationMode
    batch_size: int
    commit_interval: int
    parallel_table_count: int
    on_error: ErrorPolicy
    safety: SafetyOptions
    verification: VerificationOptions
```

## 7. Adapter 인터페이스

```python
class SourceAdapter(Protocol):
    def test_connection(self) -> ConnectionResult: ...
    def list_schemas(self) -> list[str]: ...
    def scan_schema(self, schema: str) -> SchemaSnapshot: ...
    def read_rows(
        self,
        table: TableRef,
        columns: list[str],
        cursor: ReadCursor | None,
        batch_size: int,
    ) -> Iterator[RowBatch]: ...
    def get_row_count(self, table: TableRef, condition: RowFilter | None) -> int: ...


class TargetAdapter(Protocol):
    def test_connection(self) -> ConnectionResult: ...
    def table_exists(self, table: TableRef) -> bool: ...
    def compare_table(self, table_schema: TableSchema) -> TableCompareResult: ...
    def generate_create_table(self, table_schema: TableSchema) -> DdlResult: ...
    def execute_ddl(self, ddl: str) -> ExecutionResult: ...
    def truncate_table(self, table: TableRef) -> ExecutionResult: ...
    def write_batch(self, table: TableRef, rows: list[RowData]) -> WriteResult: ...
    # v1.1 incremental extension
    def upsert_batch(self, table: TableRef, rows: list[RowData], keys: list[str]) -> WriteResult: ...
    def validate_row_count(self, table: TableRef, expected: int) -> ValidationResult: ...
```

## 8. 실행 흐름

```text
1. Config 로드
2. CLI 옵션으로 config override
3. password 누락 시 CLI prompt
4. Source/Target Adapter 생성
5. 접속 테스트
6. Source schema scan
7. Target table 존재 여부 확인
8. MigrationPlan 생성
9. Safety Guard 사전 점검
10. dry-run이면 DDL/리스크/수동 확인 리포트 생성 후 종료
11. DDL 실행
12. 데이터 batch 이관
13. checkpoint 저장
14. row count 검증
15. checksum sample 검증
16. optional FK 생성
17. 최종 리포트 생성
18. 작업 상태 반환
```

## 9. CLI 명령

```text
db-migrator scan --config config.yml
db-migrator dry-run --config config.yml
db-migrator migrate --config config.yml
db-migrator validate --config config.yml --job-id JOB_ID
db-migrator report --job-id JOB_ID
db-migrator resume --job-id JOB_ID
db-migrator retry-failed --job-id JOB_ID
db-migrator self-test run
```

옵션 override 예:

```text
db-migrator migrate ^
  --config config.yml ^
  --batch-size 10000 ^
  --mode ddl-and-dml ^
  --target-env staging
```

## 10. 설정 방식

설정은 `config.yml`과 CLI 옵션을 모두 지원한다.

우선순위:

```text
CLI option > config.yml > default value
```

비밀번호 정책:

- yml에 password가 있으면 그대로 사용
- yml에 password가 없으면 CLI prompt로 입력
- 환경변수 확장은 선택 지원
- 로그와 리포트에는 password를 절대 출력하지 않음

예시:

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
  tables:
    include: ["users", "orders", "order_items"]
    exclude: []
  existing_table_policy: skip
  batch_size: 10000
  commit_interval: 10000
  parallel_table_count: 1
  on_error: continue_table
  checkpoint_resume: true

incremental:
  enabled: false
  watermarks:
    users: updated_at

verification:
  row_count: true
  checksum_sample: true
  checksum_sample_size: 100
  checksum_datetime_precision: microseconds
  checksum_float_precision: 12

safety:
  is_production_protection: true
  allow_destructive_on_production: false
  require_dry_run_before_destructive: true

report:
  output_dir: ./reports
  formats: ["html", "json", "csv"]

driver:
  postgres:
    use_server_side_cursor: true
    cursor_batch_size: 10000
  mysql:
    driver: pymysql
    use_server_side_cursor: true
    cursor_class: SSCursor
    executemany_batch_size: 5000
    allow_mysqlclient: true
```

## 11. Event 발행 구조

Core Engine은 상태를 직접 출력하지 않고 이벤트로 발행한다.

멀티스레드 이관을 고려해 이벤트 발행기는 `queue.Queue` 기반으로 동작한다. `parallel_table_count > 1` 환경에서도 worker thread는 같은 thread-safe queue에 이벤트를 publish하고, CLI UI 또는 WebSocket publisher는 queue를 consume해서 실시간 로그와 진행률을 표시한다.

```python
@dataclass(frozen=True)
class MigrationEvent:
    job_id: str
    level: EventLevel
    type: EventType
    message: str
    table: TableRef | None
    progress: ProgressSnapshot | None
    payload: dict[str, Any]
    occurred_at: datetime
```

```python
class EventPublisher(Protocol):
    def publish(self, event: MigrationEvent) -> None: ...


class QueueEventPublisher:
    def __init__(self, queue: Queue[MigrationEvent]) -> None: ...
    def publish(self, event: MigrationEvent) -> None: ...
```

주요 이벤트:

- `JOB_STARTED`
- `CONNECTION_TESTED`
- `SCHEMA_SCANNED`
- `PLAN_CREATED`
- `SAFETY_WARNING`
- `DDL_STARTED`
- `DDL_COMPLETED`
- `DML_STARTED`
- `BATCH_COMMITTED`
- `CHECKPOINT_SAVED`
- `TABLE_COMPLETED`
- `TABLE_FAILED`
- `VALIDATION_COMPLETED`
- `REPORT_WRITTEN`
- `JOB_COMPLETED`
- `JOB_PARTIAL_SUCCESS`
- `JOB_FAILED`
- `JOB_CANCELLED`

CLI는 이 이벤트를 받아 `rich` progress/log로 표시한다. 추후 FastAPI는 같은 이벤트를 WebSocket으로 전송한다.

## 12. Checkpoint 정책

Checkpoint는 로컬 SQLite DB에 저장한다.

저장 대상:

- job id
- source/target dbms
- schema
- selected tables
- table status
- chunk start/end key
- offset fallback
- committed row count
- failed error message
- retry count
- watermark
- safety guard decision

상태:

```text
PENDING
RUNNING
COMPLETED
FAILED
SKIPPED
CANCELLED
```

재시작 옵션:

- 중단 지점부터 이어서 재개
- 실패 테이블만 재시도
- 특정 테이블만 재시도
- checkpoint 무시 후 처음부터 재실행

PK 또는 unique key가 있으면 keyset 기반 checkpoint를 우선 사용한다. PK가 없으면 offset 기반 checkpoint로 fallback하되 대용량 테이블에서는 risk report에 기록한다.

## 13. FK 및 의존성 정책

FK는 v1의 핵심 자동 이관 범위가 아니라 optional constraint migration으로 취급한다.

기본 정책:

- FK metadata는 스캔한다.
- 기본 CREATE TABLE DDL에는 FK를 포함하지 않는다.
- 테이블과 데이터 이관 완료 후 별도 `ALTER TABLE ADD CONSTRAINT` 단계에서 FK 생성을 시도한다.
- FK 생성은 기본 비활성화하고 `include_foreign_keys=true`일 때만 실행한다.
- FK 실패는 전체 이관을 즉시 실패시키지 않고 리포트에 기록한다.

의존성 정렬:

- FK metadata로 테이블 의존성 그래프를 만든다.
- 가능한 경우 위상 정렬로 DDL 생성 순서를 계산한다.
- 순환 참조가 있으면 FK 없는 테이블 생성으로 진행한다.
- 제외된 테이블을 참조하는 FK는 `manual_review`로 기록한다.

truncate/reload:

- FK가 있는 경우 참조 관계의 역순으로 truncate 계획을 만든다.
- MySQL `SET FOREIGN_KEY_CHECKS=0`는 production에서 기본 금지한다.
- 사용자가 명시 옵션으로 요청한 경우에도 Safety Guard 확인을 요구한다.

## 14. Target DB Safety Guard

운영 DB 사고를 막기 위해 파괴적 작업은 Safety Guard가 차단 또는 확인한다.

보호 대상:

- truncate
- drop
- overwrite
- delete sync
- 대량 upsert(v1.1)
- 기존 테이블 재생성
- foreign key check 비활성화

검증 입력:

- target environment
- target host
- target port
- target database
- target user
- 실행 옵션
- 대상 테이블 수
- 예상 row 수

정책:

- 기본값은 안전한 선택으로 둔다.
- production에서는 destructive 작업 전 dry-run 리포트가 필요하다.
- production destructive 작업은 확인 문구 입력을 요구한다.
- `prod`, `live`, `real`, `operation` 등 운영 의심 키워드가 있으면 경고한다.
- 보호 우회 옵션은 명시적으로만 허용하고 리포트에 기록한다.

## 15. Migration Failure & Edge Case Policy

### 15.1 대상 테이블이 이미 존재하는 경우

- 기본값: `skip + compare report`
- 옵션:
  - append
  - truncate_reload
  - upsert(v1.1)
  - compare_only
- production에서 truncate/drop/overwrite는 Safety Guard 대상이다.

### 15.2 PK 없는 테이블

- 전체 이관은 가능하다.
- upsert는 기본 불가하다.
- 증분 이관은 기본 제외한다.
- checkpoint는 offset 기반으로 fallback한다.
- 대용량 PK 없는 테이블은 risk report에 기록한다.

### 15.3 대용량 테이블

- 전체 데이터를 메모리에 올리지 않는다.
- PostgreSQL source read는 `psycopg` server-side cursor, 즉 named cursor 기반 streaming을 기본으로 한다.
- MySQL/MariaDB source adapter가 추가될 경우 `SSCursor` 또는 `SSDictCursor` 기반 streaming read를 강제한다.
- 단순 `SELECT * FROM table` 후 전체 fetch 방식은 금지한다.
- 모든 DB adapter의 `read_rows()` 구현은 iterator/generator 형태로 batch를 반환해야 하며, 전체 result set을 list로 반환하면 안 된다.
- cursor fetch size와 target executemany batch size는 별도 설정으로 분리한다.
- batch insert/bulk insert를 사용한다.
- batch 단위로 checkpoint를 저장한다.
- rows/sec, ETA, 현재 checkpoint를 이벤트로 발행한다.

### 15.4 대용량 컬럼 및 드라이버 제약

- PostgreSQL `BYTEA`, 대형 `TEXT`, `JSON/JSONB` 컬럼은 batch 크기에 따라 Python 프로세스 메모리와 target driver 인코딩 오류를 유발할 수 있다.
- `BYTEA`는 bytes 타입으로 유지하고 문자열 변환을 금지한다.
- `TEXT`, `JSON/JSONB`는 driver 인코딩 실패 시 테이블/컬럼/PK 또는 batch range를 error log에 남긴다.
- 대형 컬럼이 포함된 테이블은 기본 batch size보다 작은 `large_row_batch_size`를 적용할 수 있게 한다.
- MySQL/MariaDB writer는 `pymysql`을 기본으로 하되, 성능 병목이 확인되면 config로 `mysqlclient`를 선택할 수 있게 한다.
- `executemany_batch_size`, `max_allowed_packet` 위험, row payload 크기는 dry-run risk report에 기록한다.

### 15.5 Generated Column 처리

- Source scan 단계에서 generated column 여부를 `ColumnSchema.is_generated`로 추적한다.
- 생성 표현식은 가능한 경우 `generation_expression`에 저장한다.
- generated column은 target DDL 변환 시 `WARN_CONVERT` 또는 `MANUAL_REVIEW`로 리포트에 기록한다.
- Target Adapter의 DML `INSERT/UPSERT` 컬럼 목록에서는 generated column을 반드시 제외한다.
- generated column에 값을 직접 insert하려는 경로는 테스트로 차단한다.

### 15.6 사용자 중단

- Ctrl+C 입력 시 현재 batch 처리 후 안전 종료한다.
- checkpoint를 저장한다.
- 작업 상태는 `CANCELLED`로 기록한다.
- 다음 실행에서 resume 옵션을 안내한다.

### 15.7 부분 성공

작업 결과는 다음처럼 구분한다.

```text
SUCCESS
PARTIAL_SUCCESS
FAILED
CANCELLED
```

일부 테이블 실패가 있어도 설정이 `continue_table`이면 나머지 테이블을 계속 진행한다. 최종 리포트에는 실패 테이블과 재시도 명령을 기록한다.

### 15.8 권한 부족

사전 점검에서 다음 권한을 확인한다.

- source read 권한
- source metadata 조회 권한
- target create table 권한
- target insert/update 권한
- target truncate 권한
- target alter table 권한

권한 부족은 실행 전 warning 또는 blocking error로 분류한다.

### 15.9 charset/collation

- source/target charset 정보를 가능한 범위에서 스캔한다.
- target charset이 불명확하면 warning을 남긴다.
- 문자열 깨짐 가능성은 리포트에 기록한다.
- 자동 charset 변환은 driver와 DB 설정에 맡기되, 위험 항목은 명시한다.

### 15.10 timezone/date

- `timestamp with time zone`은 `WARN_CONVERT`로 분류한다.
- timezone 기준은 config에 명시할 수 있게 한다.
- 기준 timezone이 없으면 dry-run 리포트에 warning을 남긴다.

### 15.11 재실행과 중복 데이터

- append 모드 재실행은 중복 가능성을 경고한다.
- v1.0에서는 PK/unique key가 있으면 checkpoint resume을 우선 추천하고, upsert는 v1.1 증분 이관에서 사용한다.
- 실패 batch 재시도는 가능한 한 idempotent하게 설계한다.

### 15.12 리포트 생성 실패

- 이관 성공 여부와 리포트 생성 실패를 분리한다.
- 리포트 저장 실패 시 콘솔에 명확히 표시한다.
- checkpoint 저장을 우선한다.

### 15.13 증분 이관 범위

증분 이관은 삭제 추적, 이관 중 변경 데이터, transaction boundary, watermark 누락 등 변수가 많으므로 v1.0 필수 범위에서 제외한다.

- v1.0은 full migration 안정성에 집중한다.
- v1.0에서는 incremental config model과 report 정책만 설계에 남긴다.
- 실제 `watermark + upsert` 실행은 v1.1 마일스톤으로 분리한다.
- hard delete 동기화는 v1.1에서도 자동 처리하지 않고 리포트/수동 처리 대상으로 둔다.

## 16. 검증 정책

### 16.1 Row count 검증

테이블별 source row count와 target row count를 비교한다.

결과:

- matched
- mismatched
- skipped
- failed

### 16.2 Checksum Sample Verification

row count만으로 발견하기 어려운 타입 변환 손상을 감지하기 위해 샘플 checksum을 수행한다.

기본 방식:

- PK 또는 정렬 가능한 기준 컬럼을 찾는다.
- 상위 100건과 하위 100건을 샘플링한다.
- source/target row를 표준 문자열로 정규화한다.
- SHA256 checksum을 비교한다.
- 불일치 시 상세 차이 리포트를 생성한다.

정규화 규칙:

- datetime/timestamp는 config의 timezone 기준으로 변환한 뒤 ISO 8601 문자열로 정규화한다.
- fractional seconds는 `checksum_datetime_precision` 설정에 맞춰 반올림 또는 truncate한다.
- date/time 표현 차이로 인한 false positive를 줄이기 위해 source/target 모두 동일 formatter를 적용한다.
- float/double은 `checksum_float_precision` 설정에 맞춰 decimal 문자열로 정규화한다.
- numeric/decimal은 scale을 보존하되 trailing zero 정책을 명시한다.
- boolean은 `true/false` 문자열로 통일한다.
- bytes/blob은 hex 또는 base64 중 하나로 통일한다.
- JSON/JSONB는 key ordering, whitespace 제거, unicode escape 정책을 통일한 canonical JSON 문자열로 정규화한다.
- NULL은 빈 문자열과 구분되는 전용 token으로 정규화한다.

주의:

- 샘플 검증은 전체 무결성을 100% 보장하지 않는다.
- 대용량 테이블의 빠른 손상 감지용 1차 검증이다.
- 강한 검증이 필요하면 PK range checksum 또는 full checksum을 별도 옵션으로 제공한다.
- 정규화 규칙 변경은 checksum 결과에 영향을 주므로 리포트에 사용된 normalization profile을 기록한다.

## 17. 리포트 구성

리포트는 사람이 보기 좋은 HTML을 기본 산출물로 하고, 자동화와 분석을 위해 JSON/CSV를 함께 생성한다.

출력 구조:

```text
reports/
  JOB_ID/
    summary.html
    summary.json
    tables.csv
    warnings.csv
    errors.csv
    ddl/
      users.sql
      orders.sql
    failed_rows/
      users_failed.csv
```

HTML 리포트 구성:

- 작업 요약
- source/target 정보
- 실행 옵션
- Safety Guard 결과
- 테이블별 처리 결과
- row count 검증 결과
- checksum sample 결과
- 실패 테이블
- warning/manual review 항목
- unsupported object 목록
- 생성 DDL 링크
- checkpoint/resume 정보
- 재시도 추천 명령

테이블별 CSV 예시:

```csv
schema,table,status,source_rows,target_rows,duration_sec,rows_per_sec,warnings,error_message
public,users,success,1204302,1204302,87,13842,,
public,orders,failed,80321,50000,31,1612,,Duplicate key error at batch 5
```

warning JSON 예시:

```json
{
  "table": "users",
  "column": "profile",
  "source_type": "jsonb",
  "target_type": "json",
  "policy": "WARN_CONVERT",
  "message": "PostgreSQL jsonb semantic differences require review."
}
```

## 18. Optional Self-Test

Docker Compose 기반 self-test는 제품 실행의 필수 의존성이 아니다. 프로그램이 정상적으로 실제 DB 이관을 수행할 수 있는지 검증하기 위한 선택 기능이다.

일반 사용자 실행:

```text
db-migrator migrate --config config.yml
```

Docker가 필요 없다.

self-test 실행:

```text
db-migrator self-test run
```

동작:

```text
1. Docker 설치 여부 확인
2. 테스트용 PostgreSQL/MariaDB 컨테이너 실행
3. 샘플 schema/data 생성
4. PostgreSQL -> MariaDB 이관 실행
5. row count/checksum 검증
6. 결과 리포트 생성
7. 컨테이너 종료 옵션 제공
```

Docker가 없으면 명확한 메시지를 출력하고 종료한다.

```text
Docker is not installed or not running. Self-test requires Docker Desktop.
```

## 19. 테스트 전략

### 19.1 Unit Test

Docker 없이 빠르게 실행한다.

대상:

- config loader
- CLI option override
- type mapping
- identifier quoting
- DDL generation
- generated column scan and DML exclusion
- Safety Guard
- checkpoint state transition
- dependency graph/topological sort
- queue-based event publishing
- report data generation
- checksum normalization

### 19.2 Integration Test

Docker Compose를 선택적으로 사용한다.

실행 예:

```text
pytest -m integration
```

대상:

- PostgreSQL schema scan
- MySQL table creation
- batch insert
- checkpoint resume
- failed table retry
- row count validation
- checksum sample validation

## 20. 구현 마일스톤

### Milestone 1: 프로젝트 골격

- `uv` 프로젝트 생성
- 기본 패키지 구조 생성
- typer CLI entrypoint 생성
- config model/loader 구현
- 기본 event model 구현

### Milestone 2: Schema Scan & DDL Dry-run

- PostgreSQL 접속 테스트
- PostgreSQL schema scan
- Common Schema Model 생성
- MySQL 타입 매핑
- MySQL CREATE TABLE DDL 생성
- dry-run HTML/JSON/CSV 리포트 생성

### Milestone 3: DDL 실행

- Target table exists 확인
- existing table policy 구현
- Safety Guard 구현
- DDL 실행
- DDL 결과 checkpoint/report 기록

### Milestone 4: DML Batch Migration

- PostgreSQL server-side cursor 기반 streaming read
- MySQL batch insert
- 대형 row/대형 컬럼 batch size 조정
- `pymysql` writer 구현 및 `mysqlclient` 선택 전환 지점 정의
- batch checkpoint 저장
- rich progress 출력
- table success/failure 상태 기록

### Milestone 5: Resume & Retry

- SQLite checkpoint schema 구현
- resume 실행
- retry failed 실행
- user cancellation 처리

### Milestone 6: Validation & Report

- row count 검증
- checksum sample 검증
- 실패 row/error log
- 최종 HTML 리포트 개선

### Milestone 7: Optional FK & Self-Test

- FK metadata scan
- dependency graph
- optional FK ALTER TABLE
- Docker self-test 리소스 추가
- PyInstaller 패키징 검증

### Milestone 8: Incremental Migration v1.1

- watermark config
- 기간별 조건 이관
- upsert mode
- 이관 중 변경 데이터 처리 정책
- INSERT/UPDATE 정책 리포트
- DELETE 제외 리포트

## 21. 실행파일 배포 방향

Windows 실행파일은 PyInstaller로 패키징한다.

포함 대상:

- Python application code
- report template
- default config template
- self-test compose/template 파일

포함하지 않는 대상:

- Docker Engine
- Docker Desktop
- 실제 DB 서버
- 사용자 DB 접속 정보

Docker 관련 명령은 Docker Desktop이 설치된 환경에서만 동작한다.

## 22. 사이드이펙트 점검

런타임 리소스에 영향을 줄 수 있는 항목:

- batch size가 크면 target DB 메모리/락/트랜잭션 부담 증가
- `BYTEA`, 대형 `TEXT`, `JSON/JSONB` 컬럼은 Python 프로세스 메모리와 driver 인코딩 실패 위험 증가
- `pymysql`은 pure Python driver라 massive batch insert에서 병목 가능
- parallel table count가 크면 source/target DB 부하 증가
- checksum/full validation은 DB 부하와 실행 시간 증가
- checksum normalization 규칙이 느슨하면 false positive 또는 false negative 발생 가능
- self-test는 Docker 컨테이너와 로컬 포트를 사용
- 리포트/로그/failed rows 저장은 로컬 디스크 공간을 사용

기본값은 안전하게 둔다.

- `parallel_table_count=1`
- PostgreSQL server-side cursor 사용
- driver별 batch size 분리
- production destructive 작업 차단
- dry-run 우선
- FK 생성 기본 비활성화
- Docker self-test 선택 실행

## 23. 추가 작업 필요

구현 착수 전 다음 결정을 확정한다.

- Python 최소 버전
- `uv` 사용 여부 최종 확정
- MySQL driver 기본값은 `pymysql`로 시작하되 `mysqlclient` 선택 지원 여부
- HTML 리포트 디자인 수준
- incremental migration은 v1.1로 분리
- FK optional 생성을 v1에 포함할지 리포트까지만 할지
