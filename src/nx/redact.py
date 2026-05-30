import re
import string
from dataclasses import dataclass
from typing import Any, cast

import libtorrent as lt
import structlog

from .config import RedactionConfig

logger: structlog.stdlib.BoundLogger = structlog.get_logger(__name__)

_builtin_redactions: dict[str, RedactionConfig] = {
    "animebytes": RedactionConfig(
        pattern=r"^https://tracker\.animebytes\.tv/(?P<key>[^/]+)/announce$",
        template="https://tracker.animebytes.tv/{key}/announce",
    ),
}


@dataclass(frozen=True)
class RedactionRule:
    key: str
    pattern: re.Pattern[str]
    template: str


def _template_fields(template: str) -> set[str]:
    fields = set()
    for _, field_name, _, _ in string.Formatter().parse(template):
        if field_name is not None:
            fields.add(field_name)
    return fields


def build_redaction_rules(
    redactions: dict[str, RedactionConfig],
) -> list[RedactionRule]:
    merged = _builtin_redactions | redactions
    rules: list[RedactionRule] = []

    for key, redaction in merged.items():
        try:
            pattern = re.compile(redaction.pattern)
        except re.error as error:
            logger.warning("invalid redaction pattern, ignoring", key=key, error=error)
            continue

        template_fields = _template_fields(redaction.template)
        missing_fields = template_fields - set(pattern.groupindex.keys())
        if missing_fields:
            logger.warning(
                "redaction template references unknown fields, ignoring",
                key=key,
                fields=sorted(missing_fields),
            )
            continue

        rules.append(
            RedactionRule(key=key, pattern=pattern, template=redaction.template)
        )

    return rules


def redact_announce_url(url: str, rules: list[RedactionRule]) -> str:
    for rule in rules:
        match = rule.pattern.match(url)
        if match is not None:
            return rule.template
    return url


def _decode_string(value: Any) -> str | None:
    if isinstance(value, bytes):
        return value.decode()
    if isinstance(value, str):
        return value
    return None


def _encode_like(original: Any, value: str) -> Any:
    if isinstance(original, bytes):
        return value.encode()
    return value


def _redact_value(value: Any, rules: list[RedactionRule]) -> Any:
    url = _decode_string(value)
    if url is None:
        return value
    redacted = redact_announce_url(url, rules)
    return _encode_like(value, redacted)


def redact_torrent_buffer(buffer: bytes, rules: list[RedactionRule]) -> bytes:
    if not rules:
        return buffer

    obj = lt.bdecode(buffer)
    if not isinstance(obj, dict):
        return buffer

    torrent = cast(dict[Any, Any], obj)
    changed = False

    announce_key = b"announce" if b"announce" in torrent else "announce"
    if announce_key in torrent:
        redacted = _redact_value(torrent[announce_key], rules)
        if redacted != torrent[announce_key]:
            torrent[announce_key] = redacted
            changed = True

    announce_list_key = (
        b"announce-list" if b"announce-list" in torrent else "announce-list"
    )
    if announce_list_key in torrent and isinstance(torrent[announce_list_key], list):
        tiers = torrent[announce_list_key]
        for tier in tiers:
            if not isinstance(tier, list):
                continue
            for idx, value in enumerate(tier):
                redacted = _redact_value(value, rules)
                if redacted != value:
                    tier[idx] = redacted
                    changed = True

    if not changed:
        return buffer

    encoded = lt.bencode(torrent)
    assert isinstance(encoded, bytes), f"expected bytes, got {type(encoded)}"
    return encoded
