import hashlib
import io
import os
import types
from dataclasses import dataclass
from pathlib import Path
from typing import Generator, Protocol, runtime_checkable

import libtorrent as lt
import structlog
from rich.progress import (
    BarColumn,
    FileSizeColumn,
    Progress,
    TextColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)

logger: structlog.stdlib.BoundLogger = structlog.get_logger()
_max_open_files = 32


@dataclass
class File:
    offset: int
    size: int
    path: Path


@dataclass
class Announce:
    url: str
    tier: int


@dataclass
class MatchFilesResult:
    found: set[Path]
    missing: set[Path]
    error: set[Path]

    @property
    def ok(self) -> bool:
        return len(self.missing) == 0 and len(self.error) == 0


@dataclass
class Torrent:
    buffer: bytes
    info: lt.torrent_info

    infohash: str
    pc: int
    sz: int
    private: bool

    files: list[File]
    trackers: list[Announce]

    def get_piece_refs(
        self, piece_offset: int, piece_sz: int
    ) -> Generator[tuple[File, range], None, None]:
        start = piece_offset
        end = piece_offset + piece_sz

        for file in self.files:
            file_start = file.offset
            file_end = file.offset + file.size

            # piece outside file
            if start >= file_end or end <= file_start:
                continue

            # piece overlaps file, but may not be fully contained to the file
            overlap_start = max(start, file_start)
            overlap_end = min(end, file_end)

            bytes_start = overlap_start - file_start
            bytes_end = overlap_end - file_start

            yield file, range(bytes_start, bytes_end)

    def matches(self, path: Path, /, strip_components: int = 0) -> MatchFilesResult:
        return _match_files(self, path, strip_components)

    def strip_root(self) -> str | None:
        log = logger.bind(method="strip_root", id=self.infohash)

        ref: str | None = None
        for file in self.files:
            parts_sz = len(file.path.parts)
            if parts_sz == 1:
                log.debug("files at root, cannot strip root")
                return None
            if ref is None:
                ref = file.path.parts[0]
            else:
                if ref != file.path.parts[0]:
                    log.debug("multiple root components, cannot strip root")
                    return None
        return ref

    def verify_pieces(self, path: Path, /, strip_components: int = 0) -> bool:
        return verify_pieces(self, path, strip_components=strip_components)


@runtime_checkable
class FileReader(Protocol):
    def read(self, file: Path, r: range) -> bytes: ...


class DefaultFileReader:
    def __init__(self) -> None:
        self.refs: dict[str, io.BufferedReader] = {}
        self.sizes: dict[str, int] = {}
        self.usage: dict[str, int] = {}

    def __enter__(self) -> "DefaultFileReader":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: types.TracebackType | None,
        /,
    ) -> None:
        for ref in self.refs.values():
            ref.close()

    def cleanup(self) -> None:
        keys = list(self.usage.keys())
        if len(keys) <= _max_open_files:
            logger.debug("no cleanup needed", open_files=len(keys))
            return

        for idx, key in enumerate(keys):
            if idx > 3:
                break

            if self.usage[key] > 0:
                logger.debug("skipping busy file handle", file=key)
                continue

            logger.debug("closing file handle", file=key)
            self.refs[key].close()
            del self.refs[key]
            del self.sizes[key]
            del self.usage[key]

    def read(self, file: Path, r: range) -> bytes:
        key = os.fspath(file)

        if key not in self.refs:
            self.cleanup()

            self.refs[key] = open(os.fspath(file), "rb")
            self.sizes[key] = os.stat(os.fspath(file)).st_size
            self.usage[key] = self.usage.get(key, 0) + 1

        fh = self.refs[key]
        fh.seek(r.start)

        if r.stop >= self.sizes[key]:
            self.usage[key] = self.usage.get(key, 0) - 1

        return fh.read(r.stop - r.start)


def parse_torrent_buf(buffer: bytes) -> Torrent:
    """Parse torrent from buffer bytes"""
    info: lt.torrent_info = lt.torrent_info(buffer)

    fs: lt.file_storage = info.files()
    files: list[File] = []

    for file_idx in range(fs.num_files()):
        file_offset: int = fs.file_offset(file_idx)
        file_sz: int = fs.file_size(file_idx)
        file_path: Path = Path(fs.file_path(file_idx))

        files.append(
            File(
                offset=file_offset,
                size=file_sz,
                path=file_path,
            )
        )

    trackers: list[Announce] = []
    for tracker in info.trackers():
        assert isinstance(tracker, lt.announce_entry), (
            f"expected announce_entry, got {type(tracker)}"
        )
        trackers.append(Announce(url=tracker.url, tier=tracker.tier))

    return Torrent(
        buffer=buffer,
        info=info,
        infohash=info.info_hash().to_bytes().hex().upper(),
        pc=info.num_pieces(),
        sz=fs.total_size(),
        private=info.priv(),
        files=files,
        trackers=trackers,
    )


def parse_torrent(buffer: bytes) -> Torrent:
    log = logger.bind(method="parse_torrent")
    torrent = parse_torrent_buf(buffer)
    log.info("parsed", name=torrent.info.name())
    log.info("parsed files", files=torrent.files)
    log.info("parsed trackers", trackers=torrent.trackers)

    return torrent


def _match_files(
    torrent: Torrent,
    save_path: Path,
    strip_components: int = 0,
) -> MatchFilesResult:
    """
    :return: (good, bad) files matched
    """

    found: set[Path] = set()
    missing: set[Path] = set()
    error: set[Path] = set()

    for file in torrent.files:
        file_path = file.path
        if strip_components:
            parts_sz = len(file_path.parts)
            if parts_sz <= strip_components:
                raise ValueError(
                    f"cannot strip {strip_components} components from path '{file_path}', only has {parts_sz} parts"
                )
            file_path = Path(*file_path.parts[strip_components:])

        if file_path.parts and file_path.parts[0] == ".____padding_file":
            logger.debug("skipping padding file", file=file)
            continue

        full_path = save_path / file_path

        if not full_path.exists():
            missing.add(file.path)
        else:
            if full_path.is_file():
                found.add(file.path)
            else:
                error.add(file.path)
                logger.error(
                    "expected a file, not a file",
                    file=file,
                )

    return MatchFilesResult(found=found, missing=missing, error=error)


def verify_pieces(
    torrent: Torrent,
    save_path: Path,
    /,
    strip_components: int = 0,
) -> bool:
    log = logger.bind(method="verify_pieces", id=torrent.infohash)
    log.info("invoked", save_path=save_path)

    match_files = torrent.matches(save_path, strip_components=strip_components)
    if not match_files.ok:
        log.error(
            "files do not match", missing=match_files.missing, error=match_files.error
        )
        return False

    bad_pc = 0
    bad_sz = 0
    with DefaultFileReader() as reader:
        with Progress(
            TextColumn("[bold blue]Verifying pieces...", justify="right"),
            BarColumn(bar_width=None),
            "[progress.percentage]{task.percentage:>3.1f}%",
            "•",
            FileSizeColumn(),
            "•",
            TransferSpeedColumn(),
            "•",
            TimeRemainingColumn(),
        ) as progress:
            task = progress.add_task("verify", total=torrent.sz)

            for piece_idx in range(torrent.pc):
                piece_sz: int = torrent.info.piece_size(piece_idx)
                piece_offset: int = piece_idx * torrent.info.piece_length()

                buf: bytearray = bytearray()
                bytes_read_this_piece = 0

                for file, r in torrent.get_piece_refs(piece_offset, piece_sz):
                    file_path = file.path
                    if strip_components:
                        parts_sz = len(file_path.parts)
                        if parts_sz <= strip_components:
                            raise ValueError(
                                f"cannot strip {strip_components} components from path '{file_path}', only has {parts_sz} parts"
                            )
                        file_path = Path(*file_path.parts[strip_components:])

                    if file_path.parts and file_path.parts[0] == ".____padding_file":
                        chunk = b"\x00" * (r.stop - r.start)
                    else:
                        chunk = reader.read(save_path / file_path, r)

                    buf.extend(chunk)
                    bytes_read_this_piece += len(chunk)

                progress.update(task, advance=bytes_read_this_piece)

                data: bytes = bytes(buf[:piece_sz])
                actual_hash: bytes = hashlib.sha1(data).digest()
                expected_hash = torrent.info.hash_for_piece(piece_idx)
                assert isinstance(expected_hash, bytes), (
                    f"expected bytes, got {type(actual_hash)}"
                )

                if actual_hash != expected_hash:
                    files = set()
                    for file, _ in torrent.get_piece_refs(piece_offset, piece_sz):
                        files.add(file.path)

                    log.error(
                        "piece verification failed",
                        piece_idx=piece_idx,
                        files=files,
                        expected_hash=expected_hash.hex(),
                        actual_hash=actual_hash.hex(),
                    )

                    bad_pc += 1
                    bad_sz += piece_sz

    log.info(
        "verification complete",
        bad_pieces=bad_pc,
        total_pieces=torrent.pc,
        bad_size=bad_sz,
        total_size=torrent.sz,
    )

    return bad_pc == 0
