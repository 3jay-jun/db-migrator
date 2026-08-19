# Tech Debt Tracker

## P1: Parallel DML target adapter factory follow-up

- 발견: `core/dml_migration.py`는 worker별 target factory를 지원하지만 직접 `migrate_tables()`를 호출하면서 factory를 넘기지 않으면 기존 단일 target + 전역 lock 경로를 사용한다.
- 영향: application service 경로는 worker별 target adapter를 사용한다. core 단독 호출자는 thread-safe target factory를 직접 넘겨야 실제 target write 병렬화가 된다.
- 다음 리팩토링 방향: public core API 문서에 `target_factory` 계약을 명시하고, live integration self-test에서 `parallel_table_count > 1` 경로를 검증한다.
- 관련 파일: `src/db_migrator/core/dml_migration.py`, `src/db_migrator/adapters/postgres.py`, `src/db_migrator/adapters/mysql.py`
