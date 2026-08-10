# Phase 9: Operational Harness & Command Docs

## 목표

기존 Phase 1~8 구현을 실제 사용자가 실행하기 전에 점검할 수 있도록 환경 진단 명령, 명령 참조 문서, 샘플 설정 문서를 추가한다.

## SSOT 검색

- 검색 명령: `rg -n "Phase 9|TODO|placeholder|will be implemented|추가 작업 필요|미정|실제 DB|integration|self-test|smoke|sample config|example" .`
- 결과: Phase 9는 정의되어 있지 않고, 남은 공백은 command docs, sample config, 실행 전 환경 점검, placeholder 문서성 모듈이었다.

## 체크리스트

- [x] `doctor` 환경 점검 도메인 구현
- [x] `doctor` CLI 명령 추가
- [x] 필수 Python package import 점검 추가
- [x] writable output directory 점검 추가
- [x] optional tool `uv`, `docker`, `pyinstaller` 점검 추가
- [x] command reference 문서 추가
- [x] sample config 문서 추가
- [x] README 실행 흐름 갱신
- [x] doctor unit test 추가
- [x] `uv run pytest` 검증
- [x] 완료 후 이 파일을 completed로 이동하고 `05-plan.md` 갱신

## 검증 결과

```powershell
uv run db-migrator doctor
uv run db-migrator doctor --help
uv run pytest
```

- `uv run db-migrator doctor`: 성공, Python/import/writable directory/optional tool 점검 모두 OK
- `uv run db-migrator doctor --help`: 성공
- `uv run pytest`: 46 passed

## 사이드이펙트 점검

`doctor`는 DB에 접속하지 않고 Docker 컨테이너를 시작하지 않는다. output directory writable 확인을 위해 `reports/`, `checkpoints/`, `logs/`에 작은 임시 파일을 만들었다가 삭제한다.
