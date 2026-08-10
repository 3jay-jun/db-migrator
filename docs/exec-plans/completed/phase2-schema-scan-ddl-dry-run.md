# Phase 2: Schema Scan & DDL Dry-run

## 목표

PostgreSQL metadata를 Common Schema Model로 정규화하고, MySQL/MariaDB CREATE TABLE DDL과 dry-run 리포트 초안을 생성한다.

## SSOT 검색

- 검색 명령: `rg -n "Schema Scan|DDL Dry-run|CommonType|type_mapping|TableSchema|ColumnSchema|generate_create_table|dry-run|manual_review|WARN_CONVERT|identifier|PostgreSQL|MySQL" .`
- 결과: 구현은 placeholder뿐이고, 정책과 인터페이스 기준은 `03-Preplan.md`, `02-prd.md`, `05-plan.md`에 존재했다.

## 체크리스트

- [x] CommonType, type conversion policy 모델 구현
- [x] TableSchema/ColumnSchema/PK/Index 모델 확장
- [x] PostgreSQL type -> CommonType 매핑 구현
- [x] CommonType -> MySQL DDL type 매핑 구현
- [x] MySQL identifier quoting 구현
- [x] MySQL CREATE TABLE DDL 생성 구현
- [x] PostgreSQL connection test와 schema scan adapter 구현
- [x] dry-run report data와 JSON/CSV/HTML 초안 writer 구현
- [x] CLI `dry-run` 명령 추가
- [x] type mapping, identifier quoting, generated column 정책 테스트 추가
- [x] CLI help와 unit test 검증
- [x] 완료 후 이 파일을 completed로 이동하고 `05-plan.md` 갱신

## 검증 결과

- `.venv\Scripts\python.exe -m pip install -e ".[test]"`: 성공. `psycopg[binary]` 추가 의존성 설치 완료.
- `.venv\Scripts\db-migrator.exe --help`: 성공. `bootstrap`, `dry-run` 명령 노출 확인.
- `.venv\Scripts\db-migrator.exe dry-run --schema-file .\tests\fixtures\schema_snapshot.json --output-dir .\reports\phase2-smoke`: 성공. `tables=2`, `warnings=2`.
- `.venv\Scripts\pytest.exe`: 성공. 11개 unit test 통과.

## 사이드이펙트 점검

dry-run smoke 검증은 `reports/phase2-smoke`에 HTML/JSON/CSV 파일을 생성했다. 실제 DB 쓰기, Docker 컨테이너, 네트워크 포트 사용은 없었다. PostgreSQL 실제 접속 경로는 구현했지만 live DB config 없이 실행하지 않았다.
