import re
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

import httpx
import structlog

from .config import parse_config

logger: structlog.stdlib.BoundLogger = structlog.get_logger(__name__)

max_torrent_download_size = 16 * 1024 * 1024
_torrage_token_pattern = re.compile(r'getTTL\("([^"]+)"\s*\)')
_torrage_intermediate_url_pattern = re.compile(
    r'<script\s+src="(?P<path>/torrent\.php\?h=[^"]+&ttl=\d+)"\s*>\s*</script>'
)


class TorrentCacheError(Exception):
    pass


class TorrentCacheSource(Protocol):
    name: str

    def fetch(self, infohash: str) -> bytes:
        pass


@dataclass(frozen=True)
class ITorrentsSource:
    base_url: str

    @property
    def name(self) -> str:
        return urllib.parse.urlparse(self.base_url).netloc

    def fetch(self, infohash: str) -> bytes:
        return fetch_torrent_url(f"{self.base_url}/torrent/{infohash}.torrent")


@dataclass(frozen=True)
class TorrageSource:
    base_url: str = "https://torrage.info"

    @property
    def name(self) -> str:
        return urllib.parse.urlparse(self.base_url).netloc

    def fetch(self, infohash: str) -> bytes:
        config = parse_config()
        landing_url = (
            f"{self.base_url}/torrent.php?{urllib.parse.urlencode({'h': infohash})}"
        )
        headers = {"User-Agent": "Mozilla/5.0"}

        try:
            with httpx.Client(
                proxy=config.proxy, follow_redirects=True, headers=headers
            ) as client:
                landing_page = fetch_text_with_client(client, landing_url)
                intermediate_url = extract_torrage_intermediate_url(
                    landing_page, base_url=self.base_url
                )
                token_page = fetch_text_with_client(
                    client, intermediate_url, referer=landing_url
                )
                token = extract_torrage_token(token_page)
                ttl = transform_torrage_ttl(token)
                download_url = build_torrage_download_url(
                    infohash, ttl, base_url=self.base_url
                )
                return fetch_torrent_url_with_client(
                    client, download_url, referer=landing_url
                )
        except httpx.HTTPError as error:
            raise TorrentCacheError(str(error)) from error


def fetch_text(url: str, /) -> str:
    config = parse_config()
    logger.info("downloading torrent metadata", url=url, proxy=config.proxy)

    try:
        with httpx.Client(proxy=config.proxy, follow_redirects=True) as client:
            return fetch_text_with_client(client, url)
    except httpx.HTTPError as error:
        raise TorrentCacheError(str(error)) from error


def fetch_text_with_client(
    client: httpx.Client, url: str, /, *, referer: str | None = None
) -> str:
    logger.info("downloading torrent metadata", url=url)
    headers = {"Referer": referer} if referer is not None else None
    response = client.get(url, headers=headers)
    if response.status_code != 200:
        raise TorrentCacheError(f"status_code={response.status_code}")
    return response.text


def fetch_torrent_url(url: str, /) -> bytes:
    config = parse_config()
    logger.info("downloading torrent", url=url, proxy=config.proxy)

    try:
        with httpx.Client(proxy=config.proxy, follow_redirects=True) as client:
            return fetch_torrent_url_with_client(client, url)
    except httpx.HTTPError as error:
        raise TorrentCacheError(str(error)) from error


def fetch_torrent_url_with_client(
    client: httpx.Client, url: str, /, *, referer: str | None = None
) -> bytes:
    logger.info("downloading torrent", url=url)

    headers = {"Referer": referer} if referer is not None else None
    buffer = bytearray()
    with client.stream("GET", url, headers=headers) as response:
        if response.status_code != 200:
            raise TorrentCacheError(f"status_code={response.status_code}")

        for chunk in response.iter_bytes():
            if not chunk:
                continue

            if not buffer and chunk[0] != ord("d"):
                raise TorrentCacheError("invalid torrent file downloaded")

            buffer.extend(chunk)
            if len(buffer) > max_torrent_download_size:
                raise TorrentCacheError(
                    f"torrent file too large: max_size={max_torrent_download_size}"
                )

    if not buffer:
        raise TorrentCacheError("empty torrent file downloaded")

    return bytes(buffer)


def default_torrent_cache_sources() -> list[TorrentCacheSource]:
    return [
        ITorrentsSource("https://itorrents.org"),
        ITorrentsSource("https://itorrents.net"),
        TorrageSource(),
    ]


def download_from_cache(
    infohash: str, /, *, validate: Callable[[bytes], object] | None = None
) -> bytes:
    failures: list[str] = []

    for source in default_torrent_cache_sources():
        try:
            buffer = source.fetch(infohash)
            if validate is not None:
                validate(buffer)
            return buffer
        except (TorrentCacheError, RuntimeError, ValueError) as error:
            failures.append(f"{source.name}: {error}")
            logger.info(
                "failed to download torrent from cache",
                source=source.name,
                error=str(error),
            )

    raise TorrentCacheError("; ".join(failures))


def extract_torrage_intermediate_url(
    html: str, /, *, base_url: str = "https://torrage.info"
) -> str:
    match = _torrage_intermediate_url_pattern.search(html)
    if not match:
        raise TorrentCacheError("missing torrage intermediate url")
    return urllib.parse.urljoin(base_url, match.group("path"))


def extract_torrage_token(html: str) -> str:
    match = _torrage_token_pattern.search(html)
    if not match:
        raise TorrentCacheError("missing torrage ttl token")
    return match.group(1)


def transform_torrage_ttl(token: str) -> str:
    return caesar_shift(token, -12)[::-1]


def caesar_shift(text: str, shift: int) -> str:
    normalized_shift = shift % 26
    output = []

    for char in text:
        code = ord(char)
        if ord("A") <= code <= ord("Z"):
            output.append(chr(((code - ord("A") + normalized_shift) % 26) + ord("A")))
        elif ord("a") <= code <= ord("z"):
            output.append(chr(((code - ord("a") + normalized_shift) % 26) + ord("a")))
        else:
            output.append(char)

    return "".join(output)


def build_torrage_download_url(
    infohash: str, ttl: str, /, *, base_url: str = "https://torrage.info"
) -> str:
    query = urllib.parse.urlencode({"h": infohash, "ttl": ttl})
    return f"{base_url}/download.php?{query}"
