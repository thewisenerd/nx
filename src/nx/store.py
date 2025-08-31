import base64
import hashlib
import json
import typing
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import ContextManager, Protocol, TypedDict, runtime_checkable

import structlog

from .nx import Torrent

# AF42A720D65A556EB5CBE7DA5F0E0098379708C8
MAGIC = hashlib.sha1("NXFS24757".encode()).hexdigest().upper()


logger: structlog.stdlib.BoundLogger = structlog.get_logger(__name__)


@runtime_checkable
class Entry(Protocol):
    type: str
    id: str


@dataclass
class NxInternal:
    strip_components: int = field(default=0)
    ready: bool = field(default=False)
    last_verified: int | None = field(default=None)


NxMeta = TypedDict("NxMeta", {"@internal": NxInternal})


@dataclass
class TorrentEntry:
    type_value = "torrent"

    type: str
    id: str

    torrent: str
    nx: NxMeta

    def __post_init__(self):
        assert self.type == TorrentEntry.type_value, (
            f"type must be '{TorrentEntry.type_value}'"
        )

    @staticmethod
    def from_torrent(torrent: Torrent, /, strip_components: int = 0) -> "TorrentEntry":
        return TorrentEntry(
            type=TorrentEntry.type_value,
            id=torrent.infohash,
            torrent=base64.b64encode(torrent.buffer).decode(),
            nx={"@internal": NxInternal(strip_components=strip_components)},
        )

    def buffer(self) -> bytes:
        return base64.b64decode(self.torrent)


@dataclass
class Store:
    entries: list[Entry] = field(default_factory=list)

    @property
    def checksum(self) -> str:
        return (
            hashlib.sha1(
                json.dumps(
                    [
                        asdict(entry)
                        for entry in sorted(self.entries, key=lambda e: e.id)
                    ],
                    sort_keys=True,
                ).encode(),
            )
            .hexdigest()
            .upper()
        )

    def on_update(self):
        ids = set()
        for entry in self.entries:
            if entry.id in ids:
                raise ValueError(f"duplicate entry: {entry.id}")
            ids.add(entry.id)

    def upsert(self, entry: Entry) -> None:
        for idx, existing in enumerate(self.entries):
            if existing.id == entry.id:
                if existing.type != entry.type:
                    raise ValueError(
                        f"cannot change entry type, existing={existing.type}, new={entry.type}"
                    )
                self.entries[idx] = entry
                return
        self.entries.append(entry)

    def get_torrent(self, identifier: str) -> TorrentEntry | None:
        for entry in self.entries:
            if entry.id == identifier:
                if entry.type != TorrentEntry.type_value:
                    raise ValueError(
                        f"entry is not a torrent, type={entry.type}, id={entry.id}"
                    )
                return typing.cast(TorrentEntry, entry)
        return None


@dataclass
class FileStore(Store):
    magic: str = field(default=MAGIC)

    def on_update(self):
        super().on_update()


def load(path: Path, /, ignore_checksum: bool) -> FileStore:
    log = logger.bind(method="load", path=path, ignore_checksum=ignore_checksum)

    ser = path.read_text(encoding="utf-8")
    obj = json.loads(ser)

    if obj.get("magic") != MAGIC:
        raise ValueError("invalid magic")

    entries = []
    for entry in obj.get("entries", []):
        if entry.get("type") == TorrentEntry.type_value:
            nx = entry.get("nx", {})
            if "@internal" in nx:
                internal = nx["@internal"]
                internal = NxInternal(**internal)
                nx["@internal"] = internal
            entry["nx"] = nx
            entries.append(TorrentEntry(**entry))
        else:
            raise ValueError(f"unknown entry type: {entry.get('type')}")

    store = FileStore(
        magic=obj["magic"],
        entries=entries,
    )

    checksum = obj.get("checksum", "")
    if store.checksum != checksum:
        if ignore_checksum:
            log.warning(
                "ignoring checksum mismatch", expected=checksum, actual=store.checksum
            )
        else:
            raise ValueError("checksum mismatch")

    return store


class Repo(ContextManager):
    path: Path
    store: FileStore
    checksum: str
    save_path: Path

    @staticmethod
    def validate_path(path: Path) -> None:
        parent = path.parent
        if not parent.exists():
            raise FileNotFoundError(f"parent directory does not exist: {parent}")
        if not parent.is_dir():
            raise NotADirectoryError(f"parent is not a directory: {parent}")

    def __init__(self, path: Path, /, ignore_checksum: bool = False):
        self.path = path
        self.validate_path(self.path)
        self.save_path = self.path.absolute().parent

        if not self.path.exists():
            self.store = FileStore()
        else:
            self.store = load(self.path, ignore_checksum=ignore_checksum)

        self.checksum = self.store.checksum

    def flush_immediately(self):
        self.store.on_update()
        buffer = json.dumps(
            asdict(self.store)
            | {
                "checksum": self.store.checksum,
            },
            indent=2,
            sort_keys=True,
        )
        self.path.write_text(buffer, encoding="utf-8")

    def flush(self):
        if self.store.checksum != self.checksum:
            self.flush_immediately()
            self.checksum = self.store.checksum
        else:
            logger.debug("no changes to flush")

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.flush()
