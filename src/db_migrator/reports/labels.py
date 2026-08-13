from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def result_label(status: str) -> str:
    return {
        "matched": "성공",
        "completed": "성공",
        "mismatched": "실패",
        "failed": "실패",
        "skipped": "확인 필요",
    }.get(status, status)


def status_label(status: str) -> str:
    return {
        "matched": "일치",
        "completed": "완료",
        "mismatched": "불일치",
        "skipped": "건너뜀",
        "failed": "실패",
    }.get(status, status)


def option_label(value: str | None) -> str:
    if value is None or value == "":
        return "-"
    return {
        "ddl_only": "테이블 이관",
        "dml_only": "데이터만 이관",
        "ddl_and_dml": "기본 이관",
        "dry_run": "사전 점검",
        "incremental": "증분 이관",
        "manual": "수동 이관(DDL + DML)",
        "manual_ddl": "수동 이관(DDL)",
        "full": "전체 이관",
        "skip": "덮어쓰기",
        "fail": "기존 테이블이 있으면 실패",
        "compare_only": "비교만",
        "append": "추가",
        "sync": "동기화",
        "truncate_reload": "비우고 재적재",
        "overwrite": "덮어쓰기",
        "not_executed": "실행 안 함",
        "executed": "실행함",
        "microseconds": "마이크로초",
        "milliseconds": "밀리초",
        "seconds": "초",
    }.get(value, value)


def display_timestamp(value: str, timezone_name: str | None = None) -> str:
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value[:19].replace("T", " ")
    if timezone_name and timestamp.tzinfo is not None:
        try:
            timestamp = timestamp.astimezone(ZoneInfo(timezone_name))
        except ZoneInfoNotFoundError:
            pass
    elif timestamp.tzinfo is not None:
        timestamp = timestamp.astimezone()
    return timestamp.replace(microsecond=0, tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")
