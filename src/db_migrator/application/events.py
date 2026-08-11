from __future__ import annotations

from dataclasses import dataclass

from db_migrator.core.events import MigrationEvent


@dataclass(frozen=True)
class EventView:
    level: str
    event_type: str
    message: str
    table: str | None
    progress_label: str | None


def event_to_view(event: MigrationEvent) -> EventView:
    progress_label = None
    if event.progress is not None:
        completed = event.progress.completed_units
        total = event.progress.total_units
        if total:
            percent = min(completed / total * 100, 100)
            progress_label = f"{completed}/{total} ({percent:.1f}%)"
        else:
            progress_label = f"{completed} rows"
    return EventView(
        level=event.level.value,
        event_type=event.type.value,
        message=event.message,
        table=event.table,
        progress_label=progress_label,
    )
