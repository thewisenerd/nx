from dataclasses import dataclass
from enum import StrEnum
from itertools import chain
from pathlib import Path

from .nx import Torrent


class ImportStatus(StrEnum):
    IMPORTED = "imported"
    EXISTING_READY = "existing-ready"
    EXISTING_VERIFIED = "existing-verified"
    WOULD_IMPORT = "would-import"
    WOULD_VERIFY = "would-verify"
    WOULD_ORGANIZE = "would-organize"
    ORGANIZED = "organized"
    VERIFICATION_FAILED = "verification-failed"
    SINGLE_FILE = "single-file"
    NOT_FOUND = "not-found"
    AMBIGUOUS = "ambiguous"
    INVALID = "invalid"
    ERROR = "error"


@dataclass(frozen=True)
class ImportResult:
    source: Path
    status: ImportStatus
    target: Path | None = None
    infohash: str | None = None
    detail: str | None = None


def discover_torrent_files(source: Path) -> list[Path]:
    return sorted(path for path in source.rglob("*.torrent") if path.is_file())


def find_single_file_candidates(torrent: Torrent, library_root: Path) -> list[Path]:
    if len(torrent.files) != 1:
        return []

    relative_path = torrent.files[0].path
    if len(relative_path.parts) != 1:
        return []

    return sorted(
        path
        for path in library_root.rglob("*")
        if path.name == relative_path.name
        and path.is_file()
        and torrent.matches(path.parent).ok
    )


def find_torrent_roots(torrent: Torrent, library_root: Path) -> list[Path]:
    root_name = torrent.strip_root()
    if root_name is None:
        return []

    candidates = (
        path
        for path in chain([library_root], library_root.rglob("*"))
        if path.is_dir() and path.name == root_name
    )
    return sorted(
        candidate
        for candidate in candidates
        if torrent.matches(candidate, strip_components=1).ok
    )
