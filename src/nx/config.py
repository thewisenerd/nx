from dataclasses import dataclass, field
from typing import cast

import structlog.stdlib
import yaml
from xdg_base_dirs import xdg_cache_home, xdg_config_home

config_dir = xdg_config_home() / "nx"
cache_dir = xdg_cache_home() / "nx"

logger: structlog.stdlib.BoundLogger = structlog.get_logger()


@dataclass(frozen=True)
class RedactionConfig:
    pattern: str
    template: str


@dataclass
class Config:
    proxy: str | None = None
    redactions: dict[str, RedactionConfig] = field(default_factory=dict)
    secrets: dict[str, dict[str, str]] = field(default_factory=dict)


def _parse_redactions(data: dict[object, object]) -> dict[str, RedactionConfig]:
    redactions = data.get("redactions", {})
    if not isinstance(redactions, dict):
        logger.warning("redactions is not a dict, ignoring")
        return {}

    parsed: dict[str, RedactionConfig] = {}
    for key, value in redactions.items():
        if not isinstance(key, str):
            logger.warning("redaction key is not a string, ignoring", key=key)
            continue
        if not isinstance(value, dict):
            logger.warning("redaction is not a dict, ignoring", key=key)
            continue
        redaction = cast(dict[object, object], value)

        pattern = redaction.get("pattern")
        if not isinstance(pattern, str):
            logger.warning("redaction pattern is not a string, ignoring", key=key)
            continue

        template = redaction.get("template")
        if not isinstance(template, str):
            logger.warning("redaction template is not a string, ignoring", key=key)
            continue

        parsed[key] = RedactionConfig(pattern=pattern, template=template)

    return parsed


def _parse_secrets(data: dict[object, object]) -> dict[str, dict[str, str]]:
    secrets = data.get("secrets", {})
    if not isinstance(secrets, dict):
        logger.warning("secrets is not a dict, ignoring")
        return {}

    parsed: dict[str, dict[str, str]] = {}
    for redaction_key, values in secrets.items():
        if not isinstance(redaction_key, str):
            logger.warning(
                "secret redaction key is not a string, ignoring", key=redaction_key
            )
            continue
        if not isinstance(values, dict):
            logger.warning("secret values are not a dict, ignoring", key=redaction_key)
            continue
        secret_values = cast(dict[object, object], values)

        parsed_values: dict[str, str] = {}
        for key, value in secret_values.items():
            if not isinstance(key, str):
                logger.warning(
                    "secret key is not a string, ignoring",
                    redaction=redaction_key,
                    key=key,
                )
                continue
            if not isinstance(value, str):
                logger.warning(
                    "secret value is not a string, ignoring",
                    redaction=redaction_key,
                    key=key,
                )
                continue
            parsed_values[key] = value

        parsed[redaction_key] = parsed_values

    return parsed


def parse_config() -> Config:
    config_path = config_dir / "config.yaml"
    if not config_path.exists():
        return Config()

    if not config_path.is_file():
        logger.warning("config path is not a file, ignoring", path=config_path)
        return Config()

    data = yaml.safe_load(config_path.read_text())
    if not isinstance(data, dict):
        logger.warning("config file is not a dict, ignoring", path=config_path)
        return Config()

    proxy = data.get("proxy")
    if proxy is not None and not isinstance(proxy, str):
        logger.warning("proxy is not a string, ignoring", path=config_path)
        proxy = None

    redactions = _parse_redactions(data)
    secrets = _parse_secrets(data)

    return Config(proxy=proxy, redactions=redactions, secrets=secrets)
