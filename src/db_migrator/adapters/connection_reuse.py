from __future__ import annotations

from typing import Any, Callable


class ReusableConnection:
    def __init__(self, connect: Callable[[], Any], *, autocommit: bool = False) -> None:
        self._connect = connect
        self._autocommit = autocommit
        self._connection: Any | None = None

    def get(self) -> Any:
        if self._connection is None or _is_closed(self._connection):
            self._connection = self._connect()
            if self._autocommit:
                _enable_autocommit(self._connection)
        return self._connection

    def reset(self) -> None:
        connection = self._connection
        self._connection = None
        if connection is not None:
            _close_quietly(connection)

    def close(self) -> None:
        self.reset()


def _is_closed(connection: Any) -> bool:
    closed = getattr(connection, "closed", False)
    if closed:
        return True
    open_state = getattr(connection, "open", None)
    return open_state is False


def _enable_autocommit(connection: Any) -> None:
    try:
        autocommit = getattr(connection, "autocommit", None)
        if callable(autocommit):
            autocommit(True)
            return
        setattr(connection, "autocommit", True)
    except Exception:
        return


def _close_quietly(connection: Any) -> None:
    close = getattr(connection, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            return
