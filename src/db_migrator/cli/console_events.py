from __future__ import annotations

from typing import Any

from db_migrator.core.events import EventLevel, EventPublisher, EventType, MigrationEvent


class ConsoleEventPublisher(EventPublisher):
    def __init__(self, console: Any) -> None:
        self._console = console

    def publish(self, event: MigrationEvent) -> None:
        self._console.print(format_event(event))


def format_event(event: MigrationEvent) -> str:
    level = _level_label(event.level)
    table = f" table={event.table}" if event.table else ""
    progress = _progress_label(event)
    payload = _payload_label(event)
    return f"{level} {event.type.value}{table} {event.message}{progress}{payload}"


def _level_label(level: EventLevel) -> str:
    match level:
        case EventLevel.ERROR:
            return "[red]ERROR[/red]"
        case EventLevel.WARNING:
            return "[yellow]WARN[/yellow]"
        case _:
            return "[green]INFO[/green]"


def _progress_label(event: MigrationEvent) -> str:
    if event.progress is None:
        return ""
    completed = event.progress.completed_units
    total = event.progress.total_units
    if not isinstance(event.payload.get("eta_seconds"), int):
        return f" progress={completed} rows"
    percent = min((completed / total * 100) if total else 100, 100)
    return f" progress={completed}/{total} ({percent:.1f}%)"


def _payload_label(event: MigrationEvent) -> str:
    if not event.payload:
        return ""
    if event.type is EventType.BATCH_COMMITTED:
        rows_per_sec = event.payload.get("rows_per_sec")
        eta_seconds = event.payload.get("eta_seconds")
        next_offset = event.payload.get("next_offset")
        cursor_strategy = event.payload.get("cursor_strategy")
        batch_number = event.payload.get("batch_number")
        parts = [
            f"batch={batch_number}" if batch_number is not None else "",
            f"rows/sec={rows_per_sec:.0f}" if isinstance(rows_per_sec, float) else "",
            f"eta={_format_eta(eta_seconds)}" if isinstance(eta_seconds, int) else "",
            f"cursor={cursor_strategy}" if cursor_strategy else "",
            f"next_offset={next_offset}" if next_offset is not None else "",
        ]
        rendered = " ".join(part for part in parts if part)
        return f" {rendered}" if rendered else ""
    if event.type is EventType.CHECKPOINT_SAVED:
        batch_number = event.payload.get("batch_number")
        next_offset = event.payload.get("next_offset")
        cursor_strategy = event.payload.get("cursor_strategy")
        parts = [
            f"checkpoint_batch={batch_number}" if batch_number is not None else "",
            f"cursor={cursor_strategy}" if cursor_strategy else "",
            f"next_offset={next_offset}" if next_offset is not None else "",
        ]
        rendered = " ".join(part for part in parts if part)
        return f" {rendered}" if rendered else ""
    return ""


def _format_eta(seconds: int) -> str:
    minutes, remaining_seconds = divmod(seconds, 60)
    hours, remaining_minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h{remaining_minutes:02d}m{remaining_seconds:02d}s"
    if minutes:
        return f"{minutes}m{remaining_seconds:02d}s"
    return f"{remaining_seconds}s"
