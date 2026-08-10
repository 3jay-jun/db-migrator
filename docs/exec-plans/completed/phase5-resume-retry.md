# Phase 5: Resume & Retry

## 목표

SQLite checkpoint를 기준으로 중단 지점부터 재개, 실패 테이블만 재시도, 사용자 중단 시 안전 종료와 `CANCELLED` 상태 기록을 구현한다.

## SSOT 검색

- 검색 명령: `rg -n "Resume|Retry|retry failed|resume|checkpoint|CANCELLED|Ctrl\\+C|KeyboardInterrupt|FAILED|CANCELLED|Phase 5|중단|재시도" .`
- 결과: checkpoint 저장과 DML batch migration은 Phase 4에 구현됐고, resume/retry/cancel 경로는 아직 없었다. 정책 기준은 `02-prd.md`, `03-Preplan.md`, `05-plan.md`, `Codex.md`에 존재했다.

## 체크리스트

- [x] CheckpointStore에 latest checkpoint, failed tables, cancelled 저장 기능 추가
- [x] DML migration service에 resume cursor 입력 추가
- [x] retry failed 테이블 필터링 구현
- [x] KeyboardInterrupt 안전 종료와 CANCELLED checkpoint 기록 구현
- [x] resume/retry CLI 명령 추가
- [x] resume offset, retry failed, cancelled 상태 unit test 추가
- [x] `uv run pytest` 검증
- [x] 완료 후 이 파일을 completed로 이동하고 `05-plan.md` 갱신

## 검증 결과

- `uv run db-migrator --help`: 성공. `resume`, `retry-failed` 명령 노출 확인.
- `uv run db-migrator resume --help`: 성공. `--config`, `--schema-file`, `--checkpoint-db` 옵션 노출 확인.
- `uv run db-migrator retry-failed --help`: 성공. `--config`, `--schema-file`, `--checkpoint-db` 옵션 노출 확인.
- `uv run pytest`: 성공. 27개 unit test 통과.

## 사이드이펙트 점검

unit test는 fake adapter와 임시 SQLite checkpoint 파일만 사용했고 실제 DB에 쓰지 않았다. live DB에서 `resume`/`retry-failed`를 실행하면 target insert가 재실행될 수 있으므로 Phase 6 검증 리포트와 함께 운영 판단해야 한다. pytest cache/temp는 workspace 내부 `.tmp/`를 사용한다.
