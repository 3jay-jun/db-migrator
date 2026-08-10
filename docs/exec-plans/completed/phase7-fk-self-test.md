# Phase 7: Optional FK & Self-Test

## 목표

FK metadata 모델/scan, dependency graph/topological sort, optional FK ALTER TABLE 생성, Docker self-test 리소스, PyInstaller 패키징 검증 명령을 준비한다.

## SSOT 검색

- 검색 명령: `rg -n "foreign key|FK|dependency|topological|self-test|Docker|PyInstaller|Phase 7|constraint|ALTER TABLE|순환|위상" .`
- 결과: `schema/dependency.py`와 `selftest/runner.py`는 placeholder였고, FK/self-test 정책은 `03-Preplan.md`, `05-plan.md`에 존재했다.

## 체크리스트

- [x] ForeignKeySchema 모델 추가
- [x] schema snapshot JSON FK 파싱 추가
- [x] PostgreSQL FK metadata scan 추가
- [x] dependency graph/topological sort 구현
- [x] cycle/manual review 결과 모델 구현
- [x] MySQL FK ALTER TABLE DDL 생성 구현
- [x] FK 생성 실패 분리 report 모델 구현
- [x] Docker self-test runner와 CLI 명령 구현
- [x] Docker 미설치/미실행 메시지 테스트 추가
- [x] PyInstaller check 명령 추가
- [x] `uv run pytest` 검증
- [x] 완료 후 이 파일을 completed로 이동하고 `05-plan.md` 갱신

## 검증 결과

- `uv sync --extra test`: 성공. `pyinstaller` test extra 반영.
- `uv run db-migrator self-test --help`: 성공. `run` 명령 노출 확인.
- `uv run db-migrator package-check --help`: 성공.
- `uv run db-migrator package-check`: 성공. `PyInstaller is available.`
- `uv run db-migrator self-test run`: 예상된 차단. `Docker is not installed or not running. Self-test requires Docker Desktop.`
- `uv run pytest`: 성공. 39개 unit test 통과.

## 사이드이펙트 점검

unit test는 Docker를 실행하지 않았고 실제 DB에 접근하지 않았다. `self-test run`은 Docker Desktop이 없거나 미실행이면 명확한 메시지로 종료한다. Docker가 실행 중인 환경에서는 현재 구현이 compose config 사전검증까지만 수행하며 컨테이너 시작은 이후 통합 self-test 확장 범위다.
