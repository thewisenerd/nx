from dataclasses import dataclass

import structlog.stdlib
import yaml
from xdg_base_dirs import xdg_config_home, xdg_cache_home

config_dir = xdg_config_home() / "nx"
cache_dir = xdg_cache_home() / "nx"

logger: structlog.stdlib.BoundLogger = structlog.get_logger()


@dataclass
class Config:
    proxy: str | None = None


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

    return Config(proxy=proxy)
