# Phase 1: 프로젝트 골격

## 목표

`05-plan.md`의 Phase 1 범위에 따라 Python CLI 프로젝트 골격, 기본 도메인 경계, config/event 초안, 최소 테스트를 만든다.

## SSOT 검색

- 검색 명령: `rg -n "QueueEventPublisher|EventPublisher|MigrationEvent|Config|typer|pytest|db_migrator|pyproject|phase1-project-skeleton" .`
- 결과: 기존 구현 코드는 없고 관련 규칙은 `02-prd.md`, `03-Preplan.md`, `05-plan.md`, `Codex.md`에만 존재했다.

## 체크리스트

- [x] `docs/exec-plans/active/`와 `docs/exec-plans/completed/` 구조 준비
- [x] `pyproject.toml` 생성
- [x] `src/db_migrator/` 기본 패키지 구조 생성
- [x] `typer` CLI entrypoint 생성
- [x] config model/loader 초안 구현
- [x] event model과 `QueueEventPublisher` 구현
- [x] 기본 unit test 추가
- [x] CLI help 검증
- [x] unit test 검증
- [x] `05-plan.md` Phase 1 상태 갱신

## 검증 결과

- `uv --version`: 실패. 현재 PATH에 `uv`가 없다.
- `python --version`: 실패. 현재 PATH에 기본 `python` 명령이 없다.
- 번들 Python: `Python 3.12.13`
- `python -m pytest --version`: 최초 실패. 번들 Python에 `pytest`가 없었다.
- `.venv\Scripts\python.exe -m pip install -e ".[test]"`: 성공. 네트워크 권한 승인 후 의존성 설치 완료.
- `.venv\Scripts\db-migrator.exe --help`: 성공. `bootstrap` 명령 노출 확인.
- `.venv\Scripts\pytest.exe`: 성공. 5개 unit test 통과.

## 사이드이펙트 점검

DB 연결, Docker 컨테이너, 네트워크 포트는 사용하지 않았다. 의존성 설치가 필요할 경우 로컬 Python 환경의 site-packages와 캐시에만 영향을 준다.
