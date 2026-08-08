import hashlib

import libtorrent as lt
import pytest

from nx.nx import parse_torrent_buf
from nx.torrent_cache import (
    TorrentCacheError,
    build_torrage_download_url,
    caesar_shift,
    download_from_cache,
    extract_torrage_intermediate_url,
    extract_torrage_token,
    transform_torrage_ttl,
)


def _torrent_buffer(name: str, content: bytes) -> bytes:
    encoded = lt.bencode(
        {
            b"info": {
                b"name": name.encode(),
                b"piece length": 16 * 1024,
                b"pieces": hashlib.sha1(content).digest(),
                b"length": len(content),
            }
        }
    )
    assert isinstance(encoded, bytes)
    return encoded


def test_extract_torrage_token_allows_linebreak_before_closing_paren() -> None:
    html = 'getTTL("1VYiGKipUv1jXkBvA-uBx4yghr5ulIAHcDUIff1p5J09Vocu"\n);'

    assert extract_torrage_token(html) == (
        "1VYiGKipUv1jXkBvA-uBx4yghr5ulIAHcDUIff1p5J09Vocu"
    )


def test_extract_torrage_token_requires_getttl_call() -> None:
    with pytest.raises(TorrentCacheError):
        extract_torrage_token("no token here")


def test_extract_torrage_intermediate_url_from_landing_page() -> None:
    html = '<script src="/torrent.php?h=ABC&ttl=123"></script>'

    assert extract_torrage_intermediate_url(html) == (
        "https://torrage.info/torrent.php?h=ABC&ttl=123"
    )


def test_transform_torrage_ttl_matches_obfuscated_javascript() -> None:
    token = "1VYiGKipUv1jXkBvA-uBx4yghr5ulIAHcDUIff1p5J09Vocu"

    assert transform_torrage_ttl(token) == (
        "iqcJ90X5d1ttWIRqVOWzi5fvum4lPi-OjPyLx1jIdwYUwMJ1"
    )


def test_caesar_shift_preserves_non_letters_and_case() -> None:
    assert caesar_shift("Az-by-09", -12) == "On-pm-09"


def test_build_torrage_download_url_encodes_query() -> None:
    assert build_torrage_download_url("ABC", "a+b/") == (
        "https://torrage.info/download.php?h=ABC&ttl=a%2Bb%2F"
    )


def test_download_from_cache_rejects_torrent_with_wrong_infohash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Source:
        def __init__(self, name: str, buffer: bytes) -> None:
            self.name = name
            self.buffer = buffer

        def fetch(self, infohash: str) -> bytes:
            return self.buffer

    poisoned_buffer = _torrent_buffer("poisoned", b"bad")
    expected_buffer = _torrent_buffer("expected", b"good")
    expected_infohash = parse_torrent_buf(expected_buffer).infohash
    monkeypatch.setattr(
        "nx.torrent_cache.default_torrent_cache_sources",
        lambda: [
            Source("poisoned", poisoned_buffer),
            Source("expected", expected_buffer),
        ],
    )

    assert download_from_cache(expected_infohash) == expected_buffer
