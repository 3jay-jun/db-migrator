---
name: DB Migrator Report UI
source_service: 구름
source_slug: vapor-ui
target_surface: HTML reports
primary_override: black
last_updated: "2026-08-10"
---

# DB Migrator Report UI — design.md

## 1. Purpose

DB Migrator의 HTML 리포트 UI를 위한 프로젝트 전용 디자인 기준이다. 대상 화면은 사전 점검, 검증, 증분 이관 등 사람이 직접 검토하는 리포트이며, 1차 적용 대상은 사전 점검 리포트다.

리포트의 목적은 예쁘게 보이는 대시보드가 아니라, 운영자가 다음 결정을 빠르게 내리도록 돕는 것이다.

- 대상 테이블 생성 SQL 실행 전 변환 위험을 확인한다.
- 검토 필요 항목의 원인과 조치사항을 한 화면에서 확인한다.
- 검증 실패의 table, row identity, column, source value, target value를 한 화면에서 확인한다.
- table별 생성 SQL preview를 필요할 때 펼쳐 본다.
- HTML 단일 파일만 열어도 정보 구조와 스타일이 유지된다.

## 2. Design Source And Override

이 프로젝트는 구름 서비스의 디자인 언어를 차용한다. 적용할 핵심은 다음이다.

- light-first white canvas
- very token-driven styling
- bright, soft white surface
- 1px hairline border
- shadow 없는 card
- 정보 밀도가 높은 table/form/code surface
- factual, didactic, slightly warm tone
- 한국어 중심 안내 문장
- 장식, gradient, glass, blur, texture 배제

단, primary 색상은 프로젝트 요구사항에 따라 검정으로 오버라이드한다.

```css
--color-primary: oklch(0.000 0.000 0);
```

구름 원본 디자인의 blue primary는 이 프로젝트 리포트에서는 사용하지 않는다. 이 오버라이드는 브랜드 재현이 아니라 DB Migrator 리포트의 운영 문서 성격을 강화하기 위한 프로젝트 결정이다.

Generated UI에는 외부 디자인 시스템명, 패키지명, class prefix, source brand name을 노출하지 않는다. 이 문서에서만 출처를 기록한다.

## 3. Visual Direction

리포트는 개발자와 운영자가 장시간 읽어도 피로하지 않은 작업 화면이어야 한다.

### Personality

- 정확하다.
- 조용하다.
- 설명적이다.
- 과장하지 않는다.
- 위험 정보를 숨기지 않는다.

### Layout Mood

- 옅은 회색 page background
- 흰색 section/card surface
- 1px neutral border
- 의미 있는 상태 색상만 제한적으로 사용
- metric summary → 검토 필요 항목/actions → table 생성 SQL preview 순서

## 4. Color Tokens

OKLCH 값을 우선 사용한다. 브라우저 호환성이 문제가 되는 환경에서만 hex 변환을 허용하고, 그 경우 근사값임을 기록한다.

```css
:root {
  color-scheme: light;

  --color-canvas: oklch(1.000 0.000 0);
  --color-canvas-200: oklch(0.976 0.000 0);
  --color-overlay: oklch(1.000 0.000 0);

  --color-primary: oklch(0.000 0.000 0);
  --color-primary-soft: oklch(0.976 0.000 0);
  --color-primary-border: oklch(0.269 0.000 0);

  --color-text-strong: oklch(0.269 0.000 0);
  --color-text-normal: oklch(0.417 0.000 0);
  --color-text-muted: oklch(0.478 0.000 0);

  --color-border: oklch(0.827 0.000 0);
  --color-border-hover: oklch(0.715 0.000 0);

  --color-success-soft: oklch(0.974 0.016 167);
  --color-success-text: oklch(0.407 0.090 162);

  --color-warning-soft: oklch(0.979 0.012 51);
  --color-warning-text: oklch(0.503 0.188 33);
  --color-warning-border: oklch(0.836 0.092 46);

  --color-danger-soft: oklch(0.978 0.011 24);
  --color-danger-text: oklch(0.505 0.196 24);

  --color-code-bg: oklch(0.269 0.000 0);
}
```

### Color Roles

| Token | Role | Usage |
| --- | --- | --- |
| `--color-primary` | 프로젝트 primary | metric number, default badge, focus ring, disclosure link |
| `--color-canvas` | card/section surface | report sections, metric cards, tables |
| `--color-canvas-200` | page/subtle table header | body background, table header, row hover |
| `--color-text-strong` | high emphasis text | title, warning message |
| `--color-text-normal` | default text | table cells |
| `--color-text-muted` | secondary text | descriptions, labels, action detail |
| `--color-border` | hairline divider | card/table/section border |
| `--color-warning-*` | warning state | warning badge and warning table row emphasis |
| `--color-success-*` | clear state | no-warning badge and empty state |
| `--color-danger-*` | blocking/destructive state | future blocking report states |

## 5. Typography

Primary font stack:

```css
font-family: Pretendard, "Segoe UI", Arial, sans-serif;
```

If Pretendard is not installed, the system fallback is acceptable. HTML report generation must not require external font loading to remain portable.

| Role | Size / Line Height | Weight | Usage |
| --- | --- | --- | --- |
| Page title | 32px / 40px | 800 | report title |
| Metric number | 32px / 40px | 800 | table/warning counts |
| Section title | 18px / 26px | 700 | warning/actions, DDL preview |
| Body | 14px / 20px | 400 | descriptions and table cells |
| Table header | 13px / 20px | 700 | column labels |
| Eyebrow | 12px / 18px | 600 | page category |
| Badge | 12px / 18px | 600 | state indicators |
| Code | 12px / 18px | 400 | DDL preview |

Letter spacing은 `0`을 기본으로 한다. 리포트 UI에서는 과도하게 좁은 heading tracking을 사용하지 않는다.

## 6. Spacing

4px grid 기반 spacing scale을 사용한다.

```css
--space-050: 4px;
--space-100: 8px;
--space-150: 12px;
--space-200: 16px;
--space-250: 20px;
--space-300: 24px;
--space-400: 32px;
```

| Surface | Spacing |
| --- | --- |
| page top margin | 32px |
| page bottom margin | 48px |
| section gap | 16px |
| section padding | 24px |
| metric card padding | 16px |
| metric grid gap | 12px |
| table cell padding | 12px 16px |
| code block padding | 12px |

## 7. Radius

```css
--radius-200: 6px;
--radius-300: 8px;
--radius-400: 12px;
--radius-circle: 9999px;
```

| Surface | Radius |
| --- | --- |
| report section | 12px |
| metric card | 12px |
| code block | 8px |
| focus-visible local surface | 6px |
| badge | 9999px |

## 8. Elevation

기본 report surface에는 shadow를 사용하지 않는다.

허용:

- future popover/dialog/toast 같은 lifted surface가 생길 때만 shadow 사용

금지:

- section/card/table의 at-rest shadow
- inner shadow
- frosted glass
- backdrop blur

## 9. Report Information Architecture

사전 점검 리포트는 다음 순서를 따른다.

1. Header
2. Metric summary
3. Migration Context
4. 검토 필요 항목 및 권장 조치
5. 테이블 생성 SQL 비교

### Header

필수 요소:

- eyebrow: `사전 점검 리포트`
- H1: `DB Migrator 사전 점검 리포트`
- description: 대상 테이블 생성 SQL 실행 전 schema 변환 위험과 조치사항을 검토한다는 목적

### Metric Summary

Metric card 3개를 기본으로 한다.

- Total tables
- 검토 필요 항목 수
- 수동 검토 객체 수

### Migration Context

사전 점검 리포트에는 `이관 정보` 섹션을 metric summary 바로 아래에 배치한다. 이 섹션은 보고서 수신자가 어떤 source schema를 어떤 target database로 변환하려는 사전 점검인지 한눈에 판단하기 위한 필수 정보다.

필수 블록:

- 원본 DB: DBMS, host/port, database, schema
- 대상 DB: DBMS, host/port, database
- 사전 점검 기준: 실행 시각, 이관 모드, 기존 테이블 처리, 테이블 생성 SQL 실행 여부

금지:

- username
- password
- connection string 원문
- 기타 credential 또는 secret

### 검토 필요 항목 및 권장 조치

리포트의 핵심 영역이다. 검토 필요 항목을 숫자로만 보여주면 안 된다.

필수 컬럼:

- schema
- table
- severity
- 검토 필요 항목
- 권장 조치

검토 필요 항목이 없을 때는 단일 empty row로 안내한다.

```text
검토 필요 항목이 없습니다. 대상 테이블 생성 SQL 실행 검토 단계로 진행할 수 있습니다.
```

### 테이블 생성 SQL 비교

생성 SQL은 table별 `<details>`로 접어둔다. 기본 화면에서는 schema/table/검토 필요 항목 상태를 먼저 보여주고, SQL은 필요할 때 펼쳐 본다.

DDL code block은 dark neutral surface를 사용하고 horizontal scroll을 허용한다.

## 10. Component Rules

### Section

- background: `--color-canvas`
- border: `1px solid --color-border`
- radius: `--radius-400`
- padding: `--space-300`
- shadow 없음

### Metric Card

- background: `--color-canvas`
- border: `1px solid --color-border`
- radius: `--radius-400`
- label: muted 13px
- value: primary black 32px / 800

### Table

- `border-collapse: collapse`
- header background: `--color-canvas-200`
- header text: muted 13px / 700
- cell text: normal 14px / 20px
- row divider: 1px hairline
- row hover: subtle canvas background only
- hover transform 금지

### Badge

Badge는 22px 높이, 좌우 8px padding, pill radius를 사용한다.

| Variant | Style |
| --- | --- |
| default | black background + white text |
| warning | warning soft background + warning text + warning border |
| ok | success soft background + success text |

### Disclosure

`summary`는 primary black을 사용한다. hover 시 text-normal로만 약하게 변화한다. focus-visible은 2px primary ring과 2px offset을 유지한다.

### Code Block

- background: `--color-code-bg`
- text: light neutral
- font-size: 12px
- line-height: 18px
- radius: 8px
- overflow-x: auto

## 11. Warning Action Rules

검토 필요 항목은 반드시 조치사항과 함께 노출한다. action mapping은 report writer의 SSOT 함수와 일치해야 한다.

| Warning type | Recommended action |
| --- | --- |
| `timestamp with time zone`, `timestamptz` | source timezone 정책을 확인한다. 절대 시간이 보존되어야 하면 UTC normalize 후 migration하고 sample validation을 수행한다. |
| `jsonb` | PostgreSQL jsonb operator/index 사용 여부를 확인한다. MySQL JSON은 PostgreSQL jsonb와 query/index semantics가 다르다. |
| `uuid` | `char(36)` 저장 허용 여부와 PK/index 크기 및 성능을 검토한다. |
| generated column | MySQL/MariaDB용 generated expression으로 재작성하고 apply-ddl 전 테스트한다. |
| array | JSON text 또는 child table 등 명시적인 target model을 정한다. |
| unknown/manual review | 명시적인 type mapping 또는 custom migration rule을 정의한다. |

## 12. Responsive Behavior

현재 report는 desktop/tablet review를 1차 대상으로 한다.

| Width | Behavior |
| --- | --- |
| Desktop | table layout 유지 |
| Tablet | max-width 안에서 table column 유지 |
| Mobile | 향후 row card stack으로 전환 |

모바일 확장 시에도 warning과 recommended action은 분리하지 않는다.

## 13. Validation Report Requirements

검증 리포트는 단순히 `matched` / `mismatched` 상태만 보여주면 안 된다. 불일치가 있으면 운영자가 바로 원인을 좁힐 수 있도록 다음 정보를 HTML에 표시한다.

필수 섹션:

- Metric summary: 검증 결과, 총 테이블 수, 전체 데이터 수, 총 이관 수, 이슈 테이블 수, 스키마 객체 이슈 수
- 검증 대상 및 기준: source/target DBMS, host/port, database/schema, 실행 시각, 실행 방식, 기존 테이블 처리, 시간대 기준, 일시 비교 정밀도
- 테이블별 검증 요약: 스키마, 테이블명, 검증 결과, 전체 데이터 수, 이관 수, 행 수 차이, 대표 이슈, 다음 조치
- Issues & Recommended Actions: row count/checksum 단위 이슈, 조치사항, checksum sample 값 차이
- 검산 샘플: 테이블별 검증 요약 row를 펼쳤을 때 table별 정상 매칭 sample 최대 3건의 원본 -> 대상 값

`검증 대상 및 기준`은 metric summary 바로 아래에 배치한다. 비밀번호와 사용자명은 표시하지 않는다. host/port, database, schema는 보고서 수신자가 어떤 환경을 검증했는지 판단하는 최소 정보로 표시한다.

HTML report는 문제 해결에 필요한 이슈만 보여준다. 전체 table별 row count/checksum 원장은 HTML에 반복 노출하지 않고 `tables.csv`에 저장한다.

`Issues & Recommended Actions`에서 checksum mismatch table은 table row 전체를 `<details>` summary로 렌더링한다. table name을 클릭하면 같은 issue 아래에 table 전체 width를 사용하는 diff panel을 펼쳐 본다.

`검산 샘플`은 별도 섹션으로 분리하지 않고 `테이블별 검증 요약` row의 펼침 영역에 배치한다. 펼치면 row identity, 원본 값, 대상 값을 보여준다. 이 섹션은 전체 데이터 정합성을 증명하는 용도가 아니라 운영자가 “정상적으로 들어간 데이터도 눈으로 확인”하는 검산용이다.

펼침 영역 필수 컬럼:

- row identity
- column
- source value
- target value

주의:

- sample diff는 전체 데이터 diff가 아니라 `verification.checksum_sample_size` 범위의 sample 비교 결과다.
- 그래도 hash만 표시하는 것보다 훨씬 빠르게 mismatch 원인을 좁힐 수 있으므로 Issues table 내부에서 반드시 노출한다.
- matched sample은 table별 최대 3건만 보여준다. 더 많은 원장은 `summary.json`, `tables.csv`, DB 직접 조회로 확인한다.
- CSV 산출물 `differences.csv`에도 row identity, column, source value, target value를 저장한다.

## 14. Copy Guidelines

한국어 안내는 명확한 설명문을 기본으로 한다.

Do:

- `대상 테이블 생성 SQL을 실행하기 전에 스키마 변환 위험과 조치사항을 검토하는 리포트입니다.`
- `검토 필요 항목이 없습니다. 대상 테이블 생성 SQL 실행 검토 단계로 진행할 수 있습니다.`
- `원본 / 대상 테이블 생성 SQL 비교`

Avoid:

- 마케팅성 표현
- 감탄형 문장
- 챗봇식 권유 표현
- source service 또는 design system name을 product UI copy에 노출

## 15. Do

- token을 CSS custom properties로 먼저 정의한다.
- 검토 필요 항목과 action을 같은 table row에 표시한다.
- 생성 SQL은 접어두되 HTML 안에서 바로 열람 가능하게 한다.
- primary black은 metric, badge, focus, disclosure에만 제한적으로 쓴다.
- card/section은 1px border로 분리한다.
- HTML 단독 파일로 스타일이 유지되게 inline CSS를 유지한다.
- JSON/CSV에도 warning action 데이터를 함께 저장한다.
- validation mismatch는 가능한 경우 row identity/column/source/target value까지 표시한다.

## 16. Don't

- 검토 필요 항목 상세를 CSV/JSON에서만 확인하게 만들지 않는다.
- validation mismatch를 hash만으로 설명하지 않는다.
- gradient, glass, blur, texture, decorative illustration을 사용하지 않는다.
- report card에 기본 shadow를 넣지 않는다.
- hover/press 상태에 transform/scale을 쓰지 않는다.
- primary black을 모든 border와 본문 텍스트에 남용하지 않는다.
- 외부 디자인 시스템명, package name, class prefix를 generated UI에 노출하지 않는다.
- source service의 제품 도메인, 카피, 브랜드 정체성을 DB Migrator UI에 그대로 이식하지 않는다.

## 17. Implementation Mapping

현재 구현 위치:

| Concern | File |
| --- | --- |
| dry-run report writer | `src/db_migrator/reports/dry_run.py` |
| dry-run report tests | `tests/unit/test_dry_run_report.py` |
| sample schema fixture | `tests/fixtures/schema_snapshot.json` |
| generated preview | `reports/dry-run-preview/summary.html` |

Preview command:

```powershell
uv run db-migrator dry-run --schema-file tests\fixtures\schema_snapshot.json --output-dir reports\dry-run-preview
```

Verification command:

```powershell
uv run pytest
```
