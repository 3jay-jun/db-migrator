# Phase 3: DDL 실행과 Safety Guard

## 목표

Target table exists 확인, existing table policy, MySQL/MariaDB DDL 실행 오케스트레이션, destructive option 차단, production dry-run 선행 요구 정책을 구현한다.

## SSOT 검색

- 검색 명령: `rg -n "DDL 실행|Safety Guard|destructive|production|existing table|table_exists|execute_ddl|truncate|drop|overwrite|dry-run 선행|Phase 3" .`
- 결과: 구현은 아직 없고, 정책 기준은 `02-prd.md`, `03-Preplan.md`, `05-plan.md`, `Codex.md`에 존재했다.

## 체크리스트

- [x] migration mode와 existing table policy config 모델 추가
- [x] Safety Guard 입력/결정 모델 구현
- [x] production destructive 작업 차단 테스트 추가
- [x] production destructive 작업 dry-run 선행 요구 테스트 추가
- [x] target host/database 운영 의심 키워드 warning 테스트 추가
- [x] Target DDL executor 인터페이스와 fake executor 테스트 구현
- [x] MySQL target adapter에 table_exists/execute_ddl/truncate_table 구현
- [x] DDL execution service 구현
- [x] CLI `apply-ddl` 명령 추가
- [x] DDL 실행 결과 report data/JSON 기록
- [x] `uv run db-migrator --help`, `uv run pytest` 검증
- [x] 완료 후 이 파일을 completed로 이동하고 `05-plan.md` 갱신

## 검증 결과

- `uv --version`: 성공. `uv 0.12.2`.
- `uv sync --extra test`: 성공. `pymysql` 의존성 추가 반영.
- `uv run db-migrator --help`: 성공. `bootstrap`, `dry-run`, `apply-ddl` 명령 노출 확인.
- `uv run db-migrator apply-ddl --help`: 성공. `--config`, `--schema-file`, `--output-file` 옵션 노출 확인.
- `uv run pytest`: 성공. 19개 unit test 통과.

## 사이드이펙트 점검

unit test와 fake executor 검증은 실제 DB에 쓰지 않았다. `apply-ddl` 명령은 live MySQL/MariaDB config로 실행하면 target DB에 DDL을 실행할 수 있으므로 Safety Guard를 먼저 통과하도록 구현했다. pytest cache/temp는 workspace 내부 `.tmp/`로 고정했다.
