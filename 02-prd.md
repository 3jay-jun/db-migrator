# DB 마이그레이션 도구 기획서 v1

## 1. 개요

본 프로그램은 서로 다른 DBMS 간 테이블 구조와 데이터를 안전하게 이관하기 위한 Python 기반 DB 마이그레이션 도구다.

SI/SM, 웹사이트 고도화, 레거시 시스템 전환, DB 교체 프로젝트에서 반복적으로 발생하는 DB 이관 업무를 표준화하고 자동화하는 것을 목표로 한다.

초기 버전에서는 실무 적용 가능성과 개발 난이도를 고려해 PostgreSQL에서 MariaDB/MySQL로의 이관을 우선 지원한다. 다만 제품 구조는 특정 DBMS 조합에 종속되지 않고, 향후 Tibero, Oracle, MSSQL, MySQL, PostgreSQL 등 다양한 DBMS를 어댑터 방식으로 확장할 수 있도록 설계한다.

## 2. 제품 포지셔닝

이 도구는 단순히 데이터를 복사하는 프로그램이 아니라, DB 이관 작업의 사전 분석, 실행, 검증, 로그, 리포트를 하나의 흐름으로 제공하는 마이그레이션 보조 도구다.

핵심 가치는 다음과 같다.

- DB 구조 자동 분석
- 대상 DB 테이블 자동 생성
- 대용량 데이터 batch 이관
- 기간별 조건 이관
- v1.1 증분 이관 확장 가능 구조
- 오픈 전 사전 이관 및 오픈 당일 최종 이관 지원
- 실시간 로그와 진행률 제공
- 실패/재시도/검증 리포트 제공
- 향후 DBMS별 어댑터 확장

## 3. 목표

- 사용자가 AS-IS DB와 TO-BE DB 접속 정보를 입력하면 연결 상태를 확인한다.
- AS-IS DB의 테이블 목록과 메타데이터를 자동 스캔한다.
- TO-BE DB에 같은 이름의 테이블이 없으면 DDL을 생성하고 실행한다.
- 테이블별 데이터를 batch 단위로 이관한다.
- 대용량 데이터 이관 시 중단, 재시도, 검증이 가능해야 한다.
- 현재 어떤 작업이 진행 중인지 실시간 로그로 확인할 수 있어야 한다.
- 오픈 전 사전 이관 후, 오픈 당일에는 checkpoint resume/retry와 검증으로 최종 이관 시간을 줄인다.
- watermark + upsert 기반 증분 이관은 v1.1 범위로 분리한다.

## 4. 주요 사용자

- SI/SM 개발자
- 웹에이전시 백엔드 개발자
- DB 마이그레이션 담당자
- 운영 전환 작업자
- 레거시 시스템 고도화 담당자

## 5. 지원 범위

### 5.1 v1 우선 지원

- Source DB: PostgreSQL
- Target DB: MariaDB/MySQL
- 구현 언어: Python
- 이관 대상:
  - 테이블
  - 컬럼
  - Primary Key
  - 기본 인덱스
  - 데이터

### 5.2 향후 지원 후보

- Tibero -> PostgreSQL
- Tibero -> MySQL/MariaDB
- Oracle -> PostgreSQL
- Oracle -> MySQL/MariaDB
- MySQL/MariaDB -> PostgreSQL
- MSSQL -> PostgreSQL/MySQL

### 5.3 v1 제외 범위

- 뷰 변환
- 함수 변환
- 프로시저 변환
- 트리거 변환
- 완전 무중단 CDC
- delete 동기화
- 양방향 동기화
- 복잡한 SQL 자동 변환
- 애플리케이션 코드 자동 수정

## 6. 핵심 시나리오

### 6.1 기본 작업 흐름

사용자는 자동 전체 이관뿐 아니라, 수동으로 테이블과 실행 옵션을 선택해서 일부만 이관할 수 있어야 한다.

전체 흐름은 다음과 같다.

```text
1. 작업 생성
   -> 작업명 입력
   -> Source DBMS 선택
   -> Target DBMS 선택

2. DB 접속
   -> AS-IS DB 접속 정보 입력
   -> TO-BE DB 접속 정보 입력
   -> 양쪽 DB 접속 테스트

3. AS-IS 스캔
   -> schema 선택
   -> 테이블 목록 조회
   -> 테이블별 row count, 컬럼, PK, 인덱스 정보 표시

4. 이관 대상 선택
   -> 전체 테이블 선택
   -> 일부 테이블 수동 선택
   -> 제외 테이블 지정
   -> 테이블별 상세 정보 확인

5. 실행 옵션 선택
   -> DDL만 생성/실행
   -> DML만 실행
   -> DDL + DML 실행
   -> truncate 후 재적재
   -> append
   -> upsert(v1.1)
   -> 기간별 조건 이관

6. 사전 점검
   -> 대상 DB에 동일 테이블 존재 여부 확인
   -> 타입 변환 위험 항목 표시
   -> 예상 이관 row 수 표시
   -> 예상 리스크 표시
   -> dry-run 리포트 생성

7. 이관 실행
   -> DDL 실행
   -> 데이터 batch 이관
   -> 실시간 로그 출력
   -> 진행률/속도/예상 완료 시간 표시

8. 검증 및 결과 확인
   -> row count 비교
   -> 실패 테이블 확인
   -> 실패 row/error log 확인
   -> 재시도 대상 선택
   -> 최종 리포트 생성
```

### 6.2 수동 테이블 선택 이관

1. 사용자가 AS-IS DB에 접속한다.
2. 프로그램이 AS-IS DB의 schema와 테이블 목록을 조회한다.
3. 사용자는 조회된 테이블 목록에서 옮길 테이블만 선택한다.
4. 각 테이블의 row count, PK 존재 여부, 예상 위험 항목을 확인한다.
5. 사용자는 테이블별 또는 전체 공통 실행 옵션을 선택한다.
6. 선택한 테이블만 DDL 생성 또는 데이터 이관 대상으로 처리한다.

테이블 목록 화면에서 제공해야 할 정보는 다음과 같다.

| 항목 | 설명 |
| --- | --- |
| 선택 여부 | 이관 대상 포함 여부 |
| schema | AS-IS schema |
| table name | 테이블명 |
| row count | 예상 이관 row 수 |
| column count | 컬럼 수 |
| primary key | PK 존재 여부 |
| target exists | TO-BE DB에 동일 테이블 존재 여부 |
| risk | 타입 변환/PK 없음/대용량 등 경고 |
| action | 생성/스킵/데이터 이관/재시도 등 |

### 6.3 전체 테이블 이관

1. 사용자가 AS-IS DB 접속 정보를 입력한다.
2. 사용자가 TO-BE DB 접속 정보를 입력한다.
3. 접속 테스트를 실행한다.
4. AS-IS DB의 테이블 목록을 조회한다.
5. 사용자가 이관할 테이블을 선택한다.
6. 대상 DB에 같은 이름의 테이블이 있는지 확인한다.
7. 테이블이 없으면 DDL을 생성하고 실행한다.
8. 테이블별 데이터를 batch 단위로 이관한다.
9. 이관 완료 후 row count를 검증한다.
10. 작업 결과 리포트를 생성한다.

### 6.4 기간별 조건 이관

1. 사용자가 기간 기준 컬럼을 선택한다.
2. 예: created_at, updated_at, reg_dt, mod_dt
3. 이관 시작일과 종료일을 입력한다.
4. 해당 기간 조건에 맞는 데이터만 조회한다.
5. v1.0은 대상 DB에 insert한다. upsert는 v1.1 증분 이관에서 사용한다.
6. 기준 기간별 처리 결과를 로그와 리포트로 남긴다.

### 6.5 오픈 당일 최종 마이그레이션

1. D-1에 전체 데이터 또는 대부분의 데이터를 미리 이관한다.
2. Checkpoint DB에 테이블별/batch별 성공 지점을 저장한다.
3. 오픈 당일에는 실패 테이블 retry, checkpoint resume, 필요한 테이블의 선택 재이관을 우선 사용한다.
4. row count와 checksum sample을 검증한다.
5. 최종 리포트 확인 후 서비스 전환한다.

v1.0은 full migration 안정성, 중단 후 재개, 실패 테이블 재시도, 검증 리포트를 핵심 범위로 둔다. 마지막 watermark 이후 데이터만 반영하는 증분 실행은 v1.1 범위로 분리한다.

### 6.6 실행 모드별 동작

| 실행 모드 | 동작 | 사용 상황 |
| --- | --- | --- |
| DDL only | 테이블 생성 SQL만 생성하거나 실행 | TO-BE DB 구조만 먼저 준비할 때 |
| DML only | 이미 존재하는 테이블에 데이터만 이관 | DBA가 DDL을 별도로 반영한 경우 |
| DDL + DML | 테이블 생성 후 데이터까지 이관 | 신규 환경으로 전체 이관할 때 |
| Dry-run | 실제 실행 없이 분석/DDL/리스크 리포트만 생성 | 운영 반영 전 사전 점검 |
| Truncate + reload | 대상 테이블 데이터를 비우고 다시 적재 | 개발/테스트 DB 반복 검증 |
| Append | 기존 데이터 유지 후 추가 insert | 사전 적재 이후 추가 데이터 반영 |
| Upsert | PK 또는 unique key 기준 insert/update | v1.1 증분 이관 또는 재실행 안정성 보강 |
| Retry failed | 실패한 테이블 또는 batch만 재시도 | 대용량 이관 중 장애 복구 |

## 7. 정책 정의

### 7.1 v1.0/v1.1 이관 정책

v1.0은 전체 이관 안정성에 집중하고, 사전 이관 후 오픈 당일 사이에 발생한 데이터 변경을 자동 증분 동기화하지 않는다.

| 변경 유형 | v1.0 처리 정책 | v1.1 처리 정책 |
| --- | --- | --- |
| INSERT | checkpoint resume, retry failed, 선택 테이블 재이관으로 대응 | watermark 또는 PK range 기준 추가 이관 |
| UPDATE | row count/checksum 검증으로 차이 감지 후 선택 재이관 | updated_at 등 변경 시각 컬럼과 upsert 기준 key가 있을 때 반영 |
| DELETE | 자동 동기화 제외, 리포트/수동 처리 | 자동 동기화 제외, 삭제 확인 리포트 유지 |

v1.1의 기본 증분 이관 방식은 `watermark + upsert`다.

- watermark 기준 컬럼은 사용자가 테이블별로 지정한다.
- 기본 후보 컬럼은 updated_at, modified_at, mod_dt, created_at, reg_dt 등이다.
- UPDATE를 지원하려면 변경 시각 컬럼과 upsert 기준 PK/unique key가 필요하다.
- 기준 컬럼이 없는 테이블은 증분 이관 대상에서 제외하고 전체 재이관 또는 수동 조건 입력을 요구한다.
- DELETE는 DB 로그나 CDC 없이는 정확한 자동 감지가 어렵기 때문에 자동 처리하지 않는다.

### 7.2 DBMS 내부 기능 대응 정책

v1은 테이블 DDL과 데이터 이관에 집중한다. DBMS 내부 기능이나 표현식은 자동 변환 가능 여부에 따라 다음처럼 분류한다.

| 분류 | 처리 방식 | 예시 |
| --- | --- | --- |
| 자동 변환 | 명확한 1:1 매핑이 있는 경우 변환 | varchar, int, date, numeric |
| 경고 후 변환 | 데이터 손실 가능성은 낮지만 의미 차이가 있는 경우 변환 후 경고 | timestamp with time zone, jsonb -> json/text |
| 수동 확인 | DBMS별 의미 차이가 크거나 자동 변환 신뢰도가 낮은 경우 리포트 처리 | enum, array, generated column, expression default |
| v1 제외 | 테이블 DDL/DML 범위를 벗어나는 경우 변환하지 않음 | view, function, procedure, trigger, window function 기반 SQL |

윈도우 함수, 복잡한 SELECT, 함수 기반 표현식은 v1의 자동 변환 대상이 아니다. 테이블 생성에 필요한 default, generated column, expression index에서 이런 요소가 발견되면 실행을 중단하지 않고 수동 확인 리포트에 기록한다.

### 7.3 중단/재시도 UX 정책

대용량 이관 중 네트워크 오류, DB 연결 종료, 프로세스 중단이 발생하면 사용자가 처음부터 다시 판단하지 않도록 checkpoint 기반 복구 흐름을 제공한다.

기본 정책은 다음과 같다.

- 로컬 SQLite 기반 Checkpoint DB에 테이블별 진행 상태를 저장한다.
- batch 또는 chunk별 성공 지점을 저장한다.
- 실패 발생 시 전체 작업을 즉시 종료하거나, 실패 테이블만 보류하고 다음 테이블을 계속 진행할 수 있다.
- 재실행 시 사용자는 `처음부터 재적재`, `중단 지점부터 이어서 재개`, `실패 테이블만 재시도` 중 선택할 수 있다.
- 1억 건 이관 중 90% 지점에서 실패한 경우 기본 추천 옵션은 `중단 지점부터 이어서 재개`다.
- checkpoint 기준은 PK/unique key가 있으면 key range를 우선 사용하고, 없으면 batch offset 또는 임시 staging 기준을 사용한다.

### 7.4 운영 DB 보호 정책

운영 DB 사고를 막기 위해 파괴적 작업에는 별도 안전장치를 둔다.

보호 대상 작업:

- truncate
- drop
- overwrite
- delete sync
- 대량 upsert(v1.1 증분 이관)
- 기존 테이블 재생성

필수 안전장치:

- Target DB 접속 시 환경 구분값을 선택한다. 예: local, dev, staging, production
- production으로 선택된 DB에서는 truncate/drop/overwrite 실행 전 강한 경고를 표시한다.
- production 대상 파괴적 작업은 dry-run 리포트 생성 후에만 실행할 수 있다.
- production 대상 파괴적 작업은 작업명, 대상 DB, 대상 테이블 수, 예상 row 수를 다시 확인하게 한다.
- CLI 환경에서는 확인 문구를 직접 입력해야 한다. 예: `CONFIRM PRODUCTION TRUNCATE`
- 기본값은 항상 안전한 선택으로 둔다. 예: skip existing table, dry-run, append
- 접속 정보에 prod, live, real, operation 등 운영 추정 키워드가 있으면 production 의심 경고를 표시한다.
- `is_production_protection` 플래그가 켜진 작업에서는 Target DB에 대한 파괴적 SQL 실행을 기본 차단한다.
- Target DB Safety Guard는 접속 URL, database name, host, port, 선택한 environment, 실행 옵션을 함께 검증한다.

## 8. 주요 기능

### 8.1 접속 관리

- Source DB 접속 정보 입력
- Target DB 접속 정보 입력
- 접속 테스트
- read-only 계정 사용 권장 안내
- 접속 정보 저장 시 암호화 또는 저장 안 함 옵션 제공

### 8.2 스키마 스캔

- 테이블 목록 조회
- 컬럼명, 타입, 길이, precision, scale 조회
- nullable 조회
- default 값 조회
- Primary Key 조회
- 기본 인덱스 조회
- row count 조회
- 시스템 테이블 제외
- schema 단위 필터링
- 특정 테이블 선택

### 8.3 DDL 생성 및 실행

- AS-IS 테이블 구조를 공통 스키마 모델로 변환
- Target DBMS에 맞는 DDL 생성
- 대상 DB에 같은 이름의 테이블이 없을 경우 생성
- 이미 존재하는 테이블은 skip 또는 비교 리포트 생성
- 생성된 DDL SQL 파일 저장
- DDL 실행 전 dry-run 지원

### 8.4 데이터 이관

- 전체 테이블 이관
- 선택 테이블 이관
- 기간별 이관
- batch 단위 이관
- commit interval 설정
- append 모드
- truncate 후 재적재 모드
- upsert 모드(v1.1 증분 이관)
- 실패 테이블만 재시도

### 8.5 대용량 처리

- 전체 데이터를 메모리에 올리지 않는 streaming read 방식 사용
- batch insert 또는 bulk insert 방식 사용
- 로컬 SQLite 기반 Checkpoint DB 저장
- 테이블 단위 checkpoint 저장
- chunk 단위 성공 지점 저장
- 실패 시 마지막 성공 지점부터 재시작
- 병렬 테이블 처리 개수 제한
- 운영 DB 부하 방지를 위한 throttle/sleep 옵션
- 처리 속도와 예상 남은 시간 표시

### 8.6 Checkpoint DB

Checkpoint DB는 중단 후 재시작을 위한 로컬 SQLite 파일이다.

저장 대상:

- 작업 ID
- Source/Target DBMS 종류
- 선택 schema
- 선택 table 목록
- 테이블별 상태
- chunk별 시작/종료 key 또는 offset
- chunk별 성공 row 수
- 마지막 성공 checkpoint
- 실패 에러 메시지
- 재시도 횟수
- 증분 이관 watermark(v1.1)

프로세스가 중단된 뒤 같은 작업을 다시 실행하면 프로그램은 Checkpoint DB를 읽고 이어서 실행 가능한 작업인지 판단한다.

재시작 옵션:

- 중단 지점부터 이어서 재개
- 실패 테이블만 재시도
- 특정 테이블만 재시도
- checkpoint 무시 후 처음부터 재실행

### 8.7 Target DB Safety Guard

Target DB Safety Guard는 운영 DB에 대한 실수성 파괴 작업을 막기 위한 보호 기능이다.

검증 대상:

- target environment
- target host
- target port
- target database name
- target user
- 실행 옵션
- 대상 테이블 수
- 예상 영향 row 수

보호 정책:

- `is_production_protection=true`이면 truncate/drop/overwrite는 기본 차단한다.
- production 환경에서 파괴적 작업은 dry-run 리포트 없이는 실행할 수 없다.
- production 환경에서 파괴적 작업을 실행하려면 확인 문구를 직접 입력해야 한다.
- 운영 의심 키워드가 감지되면 사용자가 dev/staging으로 선택했더라도 경고한다.
- 보호 모드 우회는 명시 옵션으로만 가능하며, 우회 이력은 리포트에 남긴다.

### 8.8 실시간 로그

작업자는 실행 중 다음 정보를 실시간으로 확인할 수 있어야 한다.

- 현재 단계
- 현재 처리 중인 테이블
- 전체 테이블 수
- 완료 테이블 수
- 현재 테이블 총 row 수
- 현재 이관 row 수
- 초당 처리 row 수
- 예상 남은 시간
- 실패 row 수
- 경고 메시지
- 에러 메시지
- 재시도 횟수
- 현재 checkpoint 위치
- safety guard 경고 여부

로그 예시:

```text
[2026-08-07 10:12:01] CONNECT source PostgreSQL success
[2026-08-07 10:12:03] CONNECT target MariaDB success
[2026-08-07 10:12:05] SCAN tables found: 128
[2026-08-07 10:12:10] DDL users table not found in target. Creating...
[2026-08-07 10:12:11] DDL users created
[2026-08-07 10:12:15] DML users started rows=1,204,302 batch_size=10000
[2026-08-07 10:12:30] DML users progress 250,000/1,204,302 speed=16,600 rows/sec
[2026-08-07 10:12:30] CHECKPOINT users chunk=25 committed rows=250,000
[2026-08-07 10:13:42] DML users completed rows=1,204,302
[2026-08-07 10:13:45] VALIDATE users row_count matched checksum_sample matched
```

### 8.9 검증 및 리포트

- 테이블별 row count 비교
- PK 중복 검사
- nullable 위반 검사
- Checksum Sample Verification
- 샘플 checksum 또는 PK range checksum
- 실패 테이블 목록
- 실패 row/error log
- 타입 변환 위험 항목
- 수동 확인 필요 항목
- 전체 작업 시간
- 테이블별 처리 시간
- 처리 row 수
- 처리 속도

Checksum Sample Verification은 단순 row count 비교로 발견하기 어려운 타입 변환 손상을 감지하기 위한 검증 기능이다.

기본 방식:

- PK 또는 정렬 가능한 기준 컬럼을 기준으로 상위 100건과 하위 100건을 샘플링한다.
- source row와 target row의 주요 컬럼 값을 표준 문자열로 정규화한다.
- 정규화된 row 값을 MD5 또는 SHA256으로 계산해 비교한다.
- checksum 불일치 시 테이블 검증 실패로 표시하고 상세 차이 리포트를 생성한다.

주의사항:

- 샘플 검증은 전체 데이터 무결성을 100% 보장하지 않는다.
- 대용량 테이블의 빠른 손상 감지용 1차 검증으로 사용한다.
- 더 강한 검증이 필요한 경우 PK range checksum 또는 전체 checksum 옵션을 별도로 제공한다.

## 9. 주요 옵션

- DDL only
- DML only
- DDL + DML
- 전체 테이블
- 선택 테이블
- 기간별 조건 이관
- truncate 후 재적재
- target에 없는 테이블만 추가 적재
- upsert(v1.1)
- 기존 테이블은 DDL만 건너뛰고 데이터 적재
- batch size
- commit interval
- parallel table count
- 실패 시 전체 중단
- 실패 테이블만 skip 후 계속 진행
- dry-run
- throttle/sleep interval
- is_production_protection
- checkpoint resume
- checksum sample verification

## 10. 타입 변환 기본 방향

v1에서는 PostgreSQL -> MariaDB/MySQL 기준 타입 매핑을 우선 제공한다.

| PostgreSQL | MariaDB/MySQL |
| --- | --- |
| varchar(n) | varchar(n) |
| text | text 또는 longtext |
| integer | int |
| bigint | bigint |
| smallint | smallint |
| numeric(p,s) | decimal(p,s) |
| boolean | tinyint(1) |
| timestamp | datetime |
| timestamp with time zone | datetime + timezone 경고 |
| date | date |
| json/jsonb | json 또는 longtext |
| bytea | blob 또는 longblob |
| uuid | char(36) |

다음 항목은 자동 변환하더라도 리포트에 경고로 표시한다.

- timezone 포함 timestamp
- jsonb
- array 타입
- enum 타입
- uuid
- generated column
- default function
- sequence/serial/identity
- 대소문자 포함 컬럼명
- MySQL 예약어 컬럼명

## 11. 아키텍처 방향

DBMS 조합별로 별도 프로그램을 만들지 않는다.

피해야 할 구조:

```text
PostgresToMysqlMigrator
TiberoToPostgresMigrator
OracleToMysqlMigrator
```

권장 구조:

```text
Source DB Adapter
  -> Common Schema Model
  -> Target DB Adapter
```

예시:

```text
PostgreSQL Schema Reader
  -> TableSchema / ColumnSchema / IndexSchema
  -> MySQL DDL Generator / MySQL Data Writer
```

새 DBMS가 source로 추가될 때 필요한 요소:

- 접속 방식
- 테이블 목록 조회
- 컬럼/PK/인덱스 조회
- 데이터 읽기 방식
- source 타입 정규화

새 DBMS가 target으로 추가될 때 필요한 요소:

- DDL 생성 방식
- 타입 매핑
- batch insert/bulk insert 방식
- truncate 방식
- upsert 방식(v1.1)
- 검증 쿼리 방식

## 12. 기술 방향

Python 기반으로 작성한다.

권장 접근:

- v1: CLI 우선 개발
- v1.5: FastAPI 또는 간단한 웹 UI 추가
- v2: 데스크톱 앱 또는 사내 배포형 UI 검토

권장 라이브러리 후보:

- PostgreSQL: psycopg
- MySQL/MariaDB: pymysql 또는 mysqlclient
- CLI: typer 또는 argparse
- 로그: logging, rich
- 설정: pydantic 기반 YAML/JSON config
- 리포트: CSV, Excel, HTML

대안:

- Python CLI 우선
  - 빠르게 만들고 테스트하기 좋다.
  - UI는 약하지만 v1 검증에 적합하다.
  - v1 추천.

- Python + FastAPI 웹 UI
  - 실시간 로그 화면과 작업 상태 확인에 좋다.
  - 초기 구현량은 CLI보다 많다.
  - v1.5 추천.

- Electron/Node UI + Python 엔진
  - 배포형 데스크톱 앱에 적합하다.
  - 초기 복잡도가 높다.
  - v2 추천.

## 13. 리스크 및 대응

| 리스크 | 대응 |
| --- | --- |
| 대용량 이관 중 네트워크 끊김 | checkpoint 기반 재시도 |
| 프로세스 재시작 후 진행 상태 손실 | SQLite 기반 Checkpoint DB에 작업/chunk 상태 저장 |
| 중복 PK 발생 | insert/upsert/skip 정책 선택 |
| 문자셋 깨짐 | source/target charset 사전 점검 |
| 타입 변환 실패 | 위험 타입 리포트 후 수동 매핑 |
| 운영 DB 부하 | batch size, 병렬 수, throttle 설정 |
| row count 불일치 | 검증 실패 리포트 및 재이관 |
| 일부 테이블 실패 | 실패 테이블만 재시도 |
| 트랜잭션 과대 | commit interval 적용 |
| 대상 테이블 이미 존재 | skip/compare/truncate 정책 선택 |
| 기준 컬럼 없는 기간별 이관 | 전체 이관 또는 수동 조건 입력 |
| 사전 이관 후 UPDATE 누락 | v1.0은 checksum 검증 후 선택 재이관, v1.1은 watermark + upsert 적용 |
| 사전 이관 후 DELETE 누락 | 자동 동기화 제외, 삭제 확인 리포트 제공 |
| DBMS 특수 기능 자동 변환 실패 | 자동 변환/경고/수동확인/v1 제외 기준으로 분류 |
| 운영 DB에서 truncate 오실행 | production 보호 모드와 확인 문구 입력 |
| target 환경 오인 | Target DB Safety Guard로 host/database/environment/옵션 교차 검증 |
| row count는 같지만 데이터 값 손상 | Checksum Sample Verification으로 상위/하위 샘플 hash 비교 |

## 14. 산출물

- 이관 실행 로그
- 테이블별 처리 결과
- 생성된 DDL SQL 파일
- 실패 row/error log
- 검증 리포트
- 수동 확인 필요 항목 리포트
- 최종 마이그레이션 요약 리포트
- 운영 DB 보호 모드 확인 이력
- v1.1 증분 이관 watermark 기록
- SQLite Checkpoint DB
- checkpoint resume 이력
- checksum sample verification 결과
- Target DB Safety Guard 검증 결과

## 15. v1 성공 기준

- PostgreSQL에서 MariaDB 개발 DB로 테이블 10개 이상 정상 이관
- 운영 MySQL 환경에서도 동일 플로우 검증
- 최소 100만 row 이상 테이블 이관 테스트 통과
- 중간 실패 후 재시도 가능
- row count 검증 가능
- 사용자가 실시간 로그로 현재 진행 상황 파악 가능
- 운영 적용 전 dry-run 리포트 생성 가능
- 생성된 DDL과 이관 결과를 파일로 보관 가능
- v1.0에서 full migration, checkpoint resume/retry, validation 흐름이 안정적으로 동작
- v1.1 증분 이관 시 INSERT와 UPDATE 처리 정책을 명확히 선택 가능
- DELETE는 자동 동기화하지 않고 리포트로 분리 가능
- 대용량 실패 후 checkpoint 기준으로 이어서 재개 가능
- production 대상 파괴적 작업은 보호 확인 단계를 통과해야 실행 가능
- SQLite Checkpoint DB를 통해 프로세스 재시작 후 자동 이어서 이관 가능
- Target DB Safety Guard가 production 의심 접속과 파괴적 옵션 조합을 차단 가능
- Row count 외 Checksum Sample Verification으로 샘플 데이터 손상 감지 가능

## 16. 한 줄 정의

다양한 DBMS 간 테이블 구조와 데이터를 안전하게 옮기기 위한 Python 기반 DB 마이그레이션 도구이며, v1에서는 PostgreSQL -> MariaDB/MySQL 이관을 우선 지원한다.
