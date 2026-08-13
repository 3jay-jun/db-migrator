# DB 마이그레이션 도구 구현 계획

## 1. 계획 목적

이 문서는 `02-prd.md`의 제품 범위와 `03-Preplan.md`의 아키텍처를 기준으로, DB 마이그레이션 도구를 에이전트가 지속적으로 구현·검증·인계할 수 있게 만드는 상위 실행 계획이다.

세부 체크리스트, 검증 명령, 샘플 config, 긴 실행 로그는 이 파일에 반복하지 않고 `docs/exec-plans/active/`의 Phase별 exec-plan에 둔다.

## 2. 작업 방식

하네스 엔지니어링 방식의 핵심은 코드를 바로 늘리는 것이 아니라, Codex가 반복 실행 가능한 환경과 문서화된 불변조건 안에서 작업하도록 만드는 것이다.

- `AGENTS.md`는 세션 진입점과 규칙 목차 역할만 하고, `Codex.md`는 상세 작업 규칙을 보관한다.
- 제품 요구사항의 진실 소스는 `02-prd.md`로 둔다.
- 아키텍처, 도메인 모델, 마일스톤의 진실 소스는 `03-Preplan.md`로 둔다.
- 이 파일은 Phase 상태와 완료 검증 요약만 관리한다.
- Phase 착수 전에는 반드시 `docs/exec-plans/active/phase{N}-{name}.md`를 만든다.
- 구현 변경은 SSOT 검색 후 기존 구조를 확장하는 방식으로 진행한다.
- 각 Phase는 “구현 가능 범위 + 검증 명령 + 완료 기준”이 있어야 착수한다.

## 3. 설계 불변조건

아래 항목은 구현 중 임의로 약화하지 않는다.

- v1.0은 `PostgreSQL -> MariaDB/MySQL` full migration 안정성에 집중한다.
- v1.1 범위인 `watermark + upsert` 증분 이관은 v1.0 실행 경로에 섞지 않는다.
- Core Engine은 CLI, rich, FastAPI, WebSocket 같은 UI 세부사항을 알지 않는다.
- DBMS 방언은 Adapter 내부에 격리한다.
- schema 변환은 `Source Metadata -> Common Schema Model -> Target DDL` 순서를 따른다.
- type 변환은 `Source Type -> CommonType -> Target Type` 순서를 따른다.
- 대용량 row는 streaming iterator/generator로 처리하며 전체 fetch를 금지한다.
- checkpoint는 SQLite에 저장하고 resume/retry 흐름을 테스트로 보호한다.
- production destructive 작업은 Safety Guard가 기본 차단한다.
- 로그와 리포트에는 password, token, API key 등 민감정보를 출력하지 않는다.

## 4. 기술 선택

### 4.1 프로젝트 관리

| 대안 | 장점 | 단점 | 추천 |
| --- | --- | --- | --- |
| uv | 빠르고 lock 기반 재현성이 좋다 | 팀에 익숙하지 않을 수 있다 | 추천 |
| poetry | 안정적이고 Python 팀 경험이 많다 | uv보다 느리고 설정이 무겁다 | 가능 |
| requirements.txt | 가장 단순하다 | 재현성과 패키징 관리가 약하다 | 비추천 |

### 4.2 CLI 프레임워크

| 대안 | 장점 | 단점 | 추천 |
| --- | --- | --- | --- |
| typer | 타입 힌트 기반 명령 정의와 도움말 생성이 좋다 | 추가 의존성이 생긴다 | 추천 |
| argparse | 표준 라이브러리라 가볍다 | 명령이 늘면 코드가 장황해진다 | 가능 |

### 4.3 MySQL/MariaDB 드라이버

| 대안 | 장점 | 단점 | 추천 |
| --- | --- | --- | --- |
| pymysql | 순수 Python이라 Windows 배포가 쉽다 | 초대용량 insert 성능 병목 가능성이 있다 | v1 기본 |
| mysqlclient | C extension 기반으로 성능이 좋다 | Windows 빌드와 패키징 부담이 크다 | 선택 옵션 |

## 5. 리포지터리 하네스 구조

Phase 1에서 다음 구조를 먼저 만든다.

```text
docs/
  exec-plans/
    active/
    completed/
    tech-debt-tracker.md
  generated/
  references/
  product-specs/
src/
  db_migrator/
tests/
reports/
checkpoints/
logs/
```

역할은 다음처럼 나눈다.

| 위치 | 역할 |
| --- | --- |
| `docs/exec-plans/active/` | 현재 진행 중인 Phase의 세부 작업 계획 |
| `docs/exec-plans/completed/` | 완료된 Phase의 기록 |
| `docs/generated/` | 코드나 DB에서 생성한 문서 |
| `docs/references/` | 외부 도구·라이브러리 참고문서 요약 |
| `docs/product-specs/` | 기능별 제품 스펙 분리본 |
| `src/db_migrator/` | 실제 Python 패키지 |
| `tests/` | 단위/통합 테스트 |

## 6. Phase 로드맵

### Phase 1: 프로젝트 골격

목표:

- `uv` 기반 Python 프로젝트 생성
- 기본 패키지 구조 생성
- `typer` CLI entrypoint 생성
- config model/loader 초안 구현
- event model과 `QueueEventPublisher` 구현
- docs/exec-plans 구조 생성

완료 기준:

- 기본 CLI help가 실행된다.
- unit test가 최소 1개 이상 통과한다.
- Phase 1 exec-plan이 completed로 이동된다.

완료 검증:

- `db-migrator --help` 실행 성공
- `pytest` unit test 5개 통과
- Phase 1 exec-plan을 `docs/exec-plans/completed/phase1-project-skeleton.md`에 기록

상태: 완료

### Phase 2: Schema Scan & DDL Dry-run

목표:

- PostgreSQL 접속 테스트
- schema/table/column/PK/index scan
- Common Schema Model 생성
- PostgreSQL type을 CommonType으로 정규화
- CommonType을 MySQL/MariaDB DDL type으로 변환
- dry-run 리포트 초안 생성

완료 기준:

- 실제 DB에 쓰지 않고 DDL과 위험 리포트를 생성한다.
- type mapping, identifier quoting, generated column 정책 테스트가 통과한다.

완료 검증:

- `db-migrator --help` 실행 성공, `dry-run` 명령 노출
- fixture 기반 `db-migrator dry-run` 실행 성공, `tables=2`, `warnings=2`
- `pytest` unit test 11개 통과
- Phase 2 exec-plan을 `docs/exec-plans/completed/phase2-schema-scan-ddl-dry-run.md`에 기록

상태: 완료

### Phase 3: DDL 실행과 Safety Guard

목표:

- Target table exists 확인
- existing table policy 구현
- MySQL/MariaDB CREATE TABLE 실행
- destructive option 사전 차단
- dry-run 선행 요구 정책 구현

완료 기준:

- production 의심 환경에서 truncate/drop/overwrite가 차단된다.
- DDL 실행 결과가 checkpoint/report data에 남는다.

완료 검증:

- `uv run db-migrator --help` 실행 성공, `apply-ddl` 명령 노출
- `uv run db-migrator apply-ddl --help` 실행 성공
- `uv run pytest` unit test 19개 통과
- Phase 3 exec-plan을 `docs/exec-plans/completed/phase3-ddl-safety-guard.md`에 기록

상태: 완료

### Phase 4: DML Batch Migration

목표:

- PostgreSQL server-side cursor 기반 streaming read 구현
- MySQL/MariaDB batch insert 구현
- batch/commit interval 분리
- 대형 row batch size 조정 지점 추가
- 진행률 이벤트와 checkpoint 저장 구현

완료 기준:

- 전체 result set을 list로 적재하지 않는다.
- batch 단위 성공 지점이 checkpoint에 저장된다.
- 실패 테이블과 성공 테이블 상태가 구분된다.

완료 검증:

- `uv run db-migrator --help` 실행 성공, `migrate-data` 명령 노출
- `uv run db-migrator migrate-data --help` 실행 성공
- `uv run pytest` unit test 23개 통과
- Phase 4 exec-plan을 `docs/exec-plans/completed/phase4-dml-batch-migration.md`에 기록

상태: 완료

### Phase 5: Resume & Retry

목표:

- SQLite checkpoint schema 구현
- resume 명령 구현
- retry failed 명령 구현
- Ctrl+C 안전 종료 처리

완료 기준:

- 중단된 작업을 마지막 성공 batch 이후부터 재개할 수 있다.
- 실패 테이블만 재시도할 수 있다.
- 취소 상태가 리포트에 남는다.

완료 검증:

- `uv run db-migrator --help` 실행 성공, `resume`, `retry-failed` 명령 노출
- `uv run db-migrator resume --help` 실행 성공
- `uv run db-migrator retry-failed --help` 실행 성공
- `uv run pytest` unit test 27개 통과
- Phase 5 exec-plan을 `docs/exec-plans/completed/phase5-resume-retry.md`에 기록

상태: 완료

### Phase 6: Validation & Report

목표:

- row count 검증
- checksum sample 검증
- checksum normalization profile 구현
- HTML/JSON/CSV 리포트 작성
- failed row/error log 저장

완료 기준:

- row count mismatch와 checksum mismatch가 명확히 구분된다.
- 최종 리포트에서 재시도 추천 명령을 확인할 수 있다.

완료 검증:

- `uv run db-migrator --help` 실행 성공, `validate` 명령 노출
- `uv run db-migrator validate --help` 실행 성공
- `uv run pytest` unit test 31개 통과
- Phase 6 exec-plan을 `docs/exec-plans/completed/phase6-validation-report.md`에 기록

상태: 완료

### Phase 7: Optional FK & Self-Test

목표:

- FK metadata scan
- dependency graph와 topological sort 구현
- optional FK 후처리 생성
- Docker Compose self-test 추가
- PyInstaller 패키징 검증

완료 기준:

- Docker가 없으면 명확한 메시지로 self-test가 종료된다.
- FK 생성 실패는 전체 이관 실패와 분리되어 리포트된다.

완료 검증:

- `uv run db-migrator self-test --help` 실행 성공
- `uv run db-migrator package-check --help` 실행 성공
- `uv run db-migrator package-check` 실행 성공
- Docker 미실행 환경에서 `self-test run`이 명확한 메시지로 차단됨
- `uv run pytest` unit test 39개 통과
- Phase 7 exec-plan을 `docs/exec-plans/completed/phase7-fk-self-test.md`에 기록

상태: 완료

### Phase 8: Incremental Migration v1.1

목표:

- watermark config 모델 확장
- 기간별 조건 이관
- upsert mode
- INSERT/UPDATE 정책 리포트
- DELETE 제외 리포트

완료 기준:

- v1.0 full migration 경로와 v1.1 incremental 경로가 분리되어 있다.
- watermark 기준 컬럼이 없는 테이블은 자동 증분 대상에서 제외된다.

완료 검증:

- `uv run db-migrator --help` 실행 성공, `migrate-incremental` 명령 노출
- `uv run db-migrator migrate-incremental --help` 실행 성공
- `uv run pytest` unit test 44개 통과
- Phase 8 exec-plan을 `docs/exec-plans/completed/phase8-incremental-v11.md`에 기록

상태: 완료

### Phase 9: Operational Harness & Command Docs

목표:

- 실행 전 환경 진단 명령 추가
- 필수 Python package import 점검
- report/checkpoint/log output directory writable 점검
- optional tool `uv`, `docker`, `pyinstaller` 점검
- CLI command reference 문서 추가
- full/incremental sample config 문서 추가
- README 안전 실행 흐름 갱신

완료 기준:

- 사용자가 실제 DB 작업 전에 로컬 환경과 출력 경로를 점검할 수 있다.
- 모든 CLI 명령의 용도와 안전한 실행 순서를 문서에서 확인할 수 있다.
- v1.0 full migration과 v1.1 incremental migration 샘플 설정이 분리되어 있다.

완료 검증:

- `uv run db-migrator doctor --help` 실행 성공
- `uv run db-migrator doctor` 실행 성공
- `uv run pytest` unit test 46개 통과
- Phase 9 exec-plan을 `docs/exec-plans/completed/phase9-operational-harness.md`에 기록

상태: 완료

## 7. Phase 착수 절차

각 Phase 시작 시 다음 순서로 진행한다.

1. `02-prd.md`, `03-Preplan.md`, `05-plan.md`를 읽는다.
2. 관련 기존 코드와 문서를 `rg`로 검색한다.
3. `docs/exec-plans/active/phase{N}-{name}.md`를 생성한다.
4. 세부 체크리스트, 검증 명령, 완료 기준을 exec-plan에만 적는다.
5. 코드를 수정한다.
6. 테스트와 정적 검증을 실행한다.
7. 체크리스트를 즉시 갱신한다.
8. 완료 시 exec-plan을 `docs/exec-plans/completed/`로 이동한다.
9. 이 파일의 Phase 상태와 검증 요약만 갱신한다.

## 8. 품질 게이트

모든 Phase는 아래 게이트를 통과해야 완료 처리한다.

- SSOT 검색 결과를 작업 보고에 남겼는가
- v1.0/v1.1 범위가 섞이지 않았는가
- Core Engine이 UI 세부사항에 의존하지 않는가
- Adapter 밖에서 DBMS 방언 SQL을 직접 만들지 않았는가
- 대용량 처리 경로가 streaming/batch 기반인가
- checkpoint/resume 영향이 있으면 테스트가 있는가
- Safety Guard 정책을 약화하지 않았는가
- 민감정보가 로그와 리포트에 노출되지 않는가
- 실제 실행한 검증 명령과 핵심 결과를 기록했는가

## 9. 지금 바로 할 다음 작업

현재 Phase 1~9는 완료됐다.

- 실제 PostgreSQL/MariaDB 연결 기반 통합 self-test를 운영 환경에서 실행한다.
- 운영 DB 적용 전 `uv run db-migrator doctor`, `dry-run`, `apply-ddl`, `migrate-data`, `validate` 순서로 진행한다.
- 필요한 경우 Phase 10을 별도 exec-plan으로 열어 live DB integration hardening을 다룬다.

실제 운영 DB credential은 config 파일 또는 환경별 secret 관리에만 둔다.

## 10. 사이드이펙트 점검

이 문서는 계획 수립만 수행하므로 런타임 리소스, DB 연결, 프로세스 시작 순서, 네트워크 포트에는 영향이 없다.

향후 구현 Phase에서는 batch size, parallel table count, Docker self-test, report/log 디스크 사용량을 Phase별 exec-plan에서 별도로 점검한다.
