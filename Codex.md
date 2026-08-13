# Codex.md — DB Migrator 상세 작업 규칙

Codex가 이 DB 마이그레이션 도구 프로젝트에서 작업할 때 따르는 상세 세션 규칙입니다.
짧은 세션 진입점은 `AGENTS.md`이고, 이 파일은 세부 운영 규칙과 체크리스트를 보관합니다.

---

## 1. 세션 시작 시 읽을 파일

아래 순서로 현재 작업 범위를 확인합니다.

1. `02-prd.md` — 제품 요구사항과 v1.0/v1.1 범위
2. `03-Preplan.md` — 아키텍처, 도메인 모델, 마일스톤, 검증 정책
3. `05-plan.md` — 현재 구현 계획과 완료 상태가 있는 경우 확인
4. `docs/exec-plans/active/` — 활성 Phase 체크리스트가 있는 경우 확인

경로 표기는 반드시 실제 디렉터리 구분자를 포함합니다. 예: `docs/exec-plans/active/phase1-project-skeleton.md`.

---

## 2. 범위 기준

- v1.0 핵심 범위는 `full migration + checkpoint resume/retry + validation/report`입니다.
- watermark + upsert 기반 incremental migration은 v1.1 범위입니다.
- v1.0 구현 중 incremental 설정 모델을 남기더라도 기본값은 `enabled: false`이며 실제 증분 실행 경로는 만들지 않습니다.
- Event 구조는 `EventPublisher` 인터페이스와 `QueueEventPublisher` 기본 구현으로 통일합니다.

---

## 3. Exec-Plan 규칙

### 문서 역할 분리

| 파일 | 역할 | 금지 사항 |
| --- | --- | --- |
| `05-plan.md` | Phase 목표 개요와 완료 검증 결과 | 세부 체크박스, 포트 번호, 명령 출력의 장문 기록 |
| `docs/exec-plans/active/phase{N}-{name}.md` | 세부 체크리스트, 검증 명령, 기대값 | `05-plan.md`와 같은 내용 중복 |
| `docs/exec-plans/completed/` | 완료된 Phase 기록 | 활성 작업처럼 수정 |

### Phase 시작 시

- 코딩 전에 `docs/exec-plans/active/phase{N}-{name}.md`를 생성하거나 기존 active plan을 확인합니다.
- 검증 명령, config 파일명, report 출력 경로 등 구현 세부값은 exec-plan에만 둡니다.

### 작업 중

- 체크리스트 항목 완료 즉시 `[ ]`를 `[x]`로 업데이트합니다.
- 요구사항 변경은 먼저 `02-prd.md` 또는 `03-Preplan.md` 중 진실 소스가 되는 문서 하나에 반영합니다.
- 같은 정책을 여러 문서에 반복해야 하면 원본 문서를 명시하고 나머지는 요약 또는 참조로 유지합니다.

### Phase 완료 시

1. exec-plan 전체 체크박스 완료 여부 확인
2. active plan을 `docs/exec-plans/completed/`로 이동
3. `05-plan.md` Phase 상태와 검증 결과 업데이트
4. 실제 실행한 검증 명령과 핵심 출력 요약 기록
5. 다음 Phase가 확정된 경우에만 새 active plan 생성

---

## 4. 변경 유형별 추가 조치

| 변경 유형 | 추가 조치 |
| --- | --- |
| DB adapter 인터페이스 변경 | `03-Preplan.md`의 Adapter 인터페이스와 테스트 범위 확인 |
| 설정 모델 변경 | sample config와 validation 테스트 업데이트 |
| Event 모델 변경 | `core/events.py`, CLI consumer, report 기록 항목 동시 확인 |
| Checkpoint schema 변경 | SQLite migration/초기화 로직과 resume 테스트 업데이트 |
| Safety Guard 정책 변경 | dry-run 리포트와 destructive 옵션 차단 테스트 업데이트 |
| 타입 매핑 정책 변경 | `schema/type_mapping.py`와 manual_review/warning 리포트 테스트 업데이트 |
| 리포트 필드 변경 | HTML/JSON/CSV writer와 snapshot/fixture 업데이트 |

---

## 5. 세션 종료 전 자가 검수

```text
[ ] SSOT 검색을 수행하고 중복 구현을 만들지 않았는가
[ ] v1.0 범위와 v1.1 incremental 범위를 섞지 않았는가
[ ] Event 발행 경로가 EventPublisher/QueueEventPublisher 규칙을 따르는가
[ ] Core Engine이 CLI/rich/FastAPI 같은 UI 세부사항에 의존하지 않는가
[ ] DBMS별 SQL 방언이 adapter 밖으로 새지 않았는가
[ ] checkpoint resume/retry 동작에 영향을 주는 변경이면 테스트 또는 검증 계획을 남겼는가
[ ] 운영 DB 파괴 작업 방지 정책을 약화하지 않았는가
[ ] 실행한 검증 명령과 결과를 사용자에게 보고할 수 있는가
```

---

## 6. 현재 Phase 상태

아직 구현 Phase가 확정되지 않았다면 `05-plan.md`를 먼저 작성하고, Phase 1 시작 전에 `docs/exec-plans/active/phase1-project-skeleton.md`를 생성합니다.
