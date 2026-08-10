# Phase 4: DML Batch Migration

## 목표

PostgreSQL streaming read, MySQL/MariaDB batch insert, batch/commit interval 분리, 대형 row batch size 조정 지점, 진행률 이벤트와 checkpoint 저장을 구현한다.

## SSOT 검색

- 검색 명령: `rg -n "DML Batch|batch|streaming|server-side cursor|write_batch|checkpoint|commit_interval|large_row|executemany|RowBatch|read_rows|Phase 4" .`
- 결과: `checkpoint.py`는 placeholder이고 DML 구현은 없었다. 정책 기준은 `02-prd.md`, `03-Preplan.md`, `05-plan.md`, `Codex.md`에 존재했다.

## 체크리스트

- [x] RowData/RowBatch/ReadCursor/WriteResult 모델 추가
- [x] Source/Target adapter DML 프로토콜 확장
- [x] PostgreSQL server-side cursor 기반 `read_rows()` 구현
- [x] MySQL/MariaDB `write_batch()` 구현
- [x] SQLite checkpoint store 최소 구현
- [x] DML migration service 구현
- [x] batch/commit interval 분리 지점 구현
- [x] large row batch size 조정 지점 구현
- [x] DML progress/checkpoint/table status 이벤트 발행
- [x] Fake adapter 기반 streaming/batch/checkpoint 테스트 추가
- [x] `uv run pytest` 검증
- [x] 완료 후 이 파일을 completed로 이동하고 `05-plan.md` 갱신

## 검증 결과

- `uv run db-migrator --help`: 성공. `migrate-data` 명령 노출 확인.
- `uv run db-migrator migrate-data --help`: 성공. `--config`, `--schema-file`, `--checkpoint-db` 옵션 노출 확인.
- `uv run pytest`: 성공. 23개 unit test 통과.

## 사이드이펙트 점검

unit test는 fake adapter와 임시 SQLite checkpoint 파일만 사용했고 실제 DB에 쓰지 않았다. `migrate-data` 명령은 live config로 실행하면 source DB streaming read와 target DB batch insert/commit을 수행하므로 batch size, commit interval, target 환경을 실행 전 확인해야 한다. pytest cache/temp는 workspace 내부 `.tmp/`를 사용한다.
