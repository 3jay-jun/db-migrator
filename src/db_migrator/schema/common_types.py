from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class TypePolicy(StrEnum):
    AUTO_CONVERT = "auto_convert"
    WARN_CONVERT = "warn_convert"
    MANUAL_REVIEW = "manual_review"
    UNSUPPORTED = "unsupported"


class CommonTypeKind(StrEnum):
    STRING = "string"
    TEXT = "text"
    INTEGER = "integer"
    BIGINT = "bigint"
    SMALLINT = "smallint"
    DECIMAL = "decimal"
    BOOLEAN = "boolean"
    DATETIME = "datetime"
    TIME = "time"
    DATE = "date"
    JSON = "json"
    BINARY = "binary"
    UUID = "uuid"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class SchemaWarning:
    code: str
    message: str
    policy: TypePolicy


@dataclass(frozen=True)
class CommonType:
    kind: CommonTypeKind
    policy: TypePolicy
    length: int | None = None
    precision: int | None = None
    scale: int | None = None
    source_type: str | None = None
    warnings: tuple[SchemaWarning, ...] = field(default_factory=tuple)

    @property
    def requires_manual_review(self) -> bool:
        return self.policy in {TypePolicy.MANUAL_REVIEW, TypePolicy.UNSUPPORTED}
