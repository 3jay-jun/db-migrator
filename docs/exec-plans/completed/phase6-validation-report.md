# Phase 6: Validation & Report

## 목표

row count 검증, checksum sample 검증, checksum normalization profile, HTML/JSON/CSV 최종 리포트, failed row/error log 초안을 구현한다.

## SSOT 검색

- 검색 명령: `rg -n "Validation|Report|row count|checksum|checksum sample|normalization|failed row|HTML|JSON|CSV|Phase 6|validate" .`
- 결과: dry-run report 초안은 있고 validation/report 본 구현은 placeholder였다. 정책 기준은 `02-prd.md`, `03-Preplan.md`, `05-plan.md`, `Codex.md`에 존재했다.

## 체크리스트

- [x] ValidationReader 프로토콜 구현
- [x] row count validation 구현
- [x] checksum sample validation 구현
- [x] checksum normalization profile 구현
- [x] final report data 모델 구현
- [x] HTML/JSON/CSV final report writer 구현
- [x] failed/error log writer 초안 구현
- [x] CLI `validate` 명령 추가
- [x] row count mismatch와 checksum mismatch 분리 테스트 추가
- [x] `uv run pytest` 검증
- [x] 완료 후 이 파일을 completed로 이동하고 `05-plan.md` 갱신

## 검증 결과

- `uv run db-migrator --help`: 성공. `validate` 명령 노출 확인.
- `uv run db-migrator validate --help`: 성공. `--config`, `--schema-file`, `--output-dir` 옵션 노출 확인.
- `uv run pytest`: 성공. 31개 unit test 통과.

## 사이드이펙트 점검

unit test는 fake validation reader와 임시 report 디렉터리만 사용했고 실제 DB에 접근하지 않았다. live DB에서 `validate`를 실행하면 source/target row count query와 checksum sample query가 발생해 DB 부하가 생길 수 있다. report writer는 output dir에 `summary.html`, `summary.json`, `tables.csv`, `errors.csv`를 생성한다.
