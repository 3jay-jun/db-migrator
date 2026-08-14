# Tech Debt Tracker

## P1: Parallel DML target write serialization

- 발견: `core/dml_migration.py`는 `parallel_table_count > 1`일 때 table worker를 만들지만 target write/commit/sync-key 작업을 전역 `target_lock` 안에서 수행한다.
- 영향: source read는 병렬화될 수 있지만 target write는 직렬화된다. 또한 현재 target adapter는 단일 `_dml_connection`을 재사용하므로 lock 제거만으로는 thread-safe하지 않다.
- 다음 리팩토링 방향: worker별 target adapter/connection factory를 주입하거나, 문서/설정에서 `parallel_table_count`를 parallel table read + serialized target write로 명시한다.
- 관련 파일: `src/db_migrator/core/dml_migration.py`, `src/db_migrator/adapters/postgres.py`, `src/db_migrator/adapters/mysql.py`
