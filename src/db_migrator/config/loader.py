from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from db_migrator.config.models import AppConfig


class ConfigLoadError(ValueError):
    pass


def load_config(config_path: Path | None) -> AppConfig:
    if config_path is None:
        return AppConfig()

    if not config_path.exists():
        raise ConfigLoadError(f"Config file does not exist: {config_path}")

    try:
        raw_config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigLoadError(f"Config file is not valid YAML: {config_path}") from exc

    if not isinstance(raw_config, dict):
        raise ConfigLoadError(f"Config file must contain a YAML object: {config_path}")

    return _parse_config(raw_config, config_path)


def _parse_config(raw_config: dict[str, Any], config_path: Path) -> AppConfig:
    try:
        return AppConfig.model_validate(raw_config)
    except ValidationError as exc:
        raise ConfigLoadError(f"Config file failed validation: {config_path}") from exc
