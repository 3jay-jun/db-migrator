# Phase 8: Incremental Migration v1.1

## 목표

watermark config, 기간별 조건 이관, upsert mode, INSERT/UPDATE 정책 리포트, DELETE 제외 리포트를 v1.0 full migration 경로와 분리해서 구현한다.

## SSOT 검색

- 검색 명령: `rg -n "incremental|watermark|upsert|기간별|조건 이관|DELETE|INSERT|UPDATE|Phase 8|v1.1|updated_at|created_at" .`
- 결과: incremental 정책은 `02-prd.md`, `03-Preplan.md`, `05-plan.md`, `Codex.md`에 있고 실행 구현은 없었다. v1.0 DML 경로는 `migrate-data`로 분리되어 있다.

## 체크리스트

- [x] IncrementalConfig/WatermarkConfig 모델 추가, 기본 `enabled=false`
- [x] incremental 전용 source reader 프로토콜 추가
- [x] watermark/기간 조건 batch read 모델 구현
- [x] target upsert writer 프로토콜과 MySQL upsert 구현
- [x] watermark 기준 컬럼 없는 테이블 제외 구현
- [x] PK/unique key 없는 테이블 upsert 제외 구현
- [x] DELETE 자동 동기화 제외 리포트 구현
- [x] incremental report JSON/CSV/HTML writer 구현
- [x] CLI `migrate-incremental` 추가
- [x] v1.0 full migration 경로와 분리 테스트 추가
- [x] `uv run pytest` 검증
- [x] 완료 후 이 파일을 completed로 이동하고 `05-plan.md` 갱신

## 검증 결과

- `uv run db-migrator --help`: 성공. `migrate-incremental` 명령 노출 확인.
- `uv run db-migrator migrate-incremental --help`: 성공. `--config`, `--schema-file`, `--output-dir` 옵션 노출 확인.
- `uv run pytest`: 성공. 44개 unit test 통과.

## 사이드이펙트 점검

unit test는 fake reader/writer와 임시 report 디렉터리만 사용했고 실제 DB에 접근하지 않았다. live `migrate-incremental`은 source 조건 조회와 target upsert를 수행한다. DELETE sync는 자동 지원하지 않고 `delete_policy.txt` 리포트로 분리한다.
