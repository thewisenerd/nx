import hashlib
from pathlib import Path

import libtorrent as lt
from click.testing import CliRunner

from nx.cli import nx
from nx.import_torrents import discover_torrent_files, find_torrent_roots
from nx.nx import parse_torrent_buf
from nx.store import DefaultStorePathName, load


def _torrent_buffer(root: str, files: dict[str, bytes]) -> bytes:
    payload = b"".join(files.values())
    info = {
        b"name": root.encode(),
        b"piece length": 16 * 1024,
        b"pieces": hashlib.sha1(payload).digest(),
        b"files": [
            {
                b"length": len(content),
                b"path": [part.encode() for part in Path(path).parts],
            }
            for path, content in files.items()
        ],
    }
    encoded = lt.bencode({b"info": info})
    assert isinstance(encoded, bytes)
    return encoded


def _single_file_torrent_buffer(name: str, content: bytes) -> bytes:
    info = {
        b"name": name.encode(),
        b"piece length": 16 * 1024,
        b"pieces": hashlib.sha1(content).digest(),
        b"length": len(content),
    }
    encoded = lt.bencode({b"info": info})
    assert isinstance(encoded, bytes)
    return encoded


def _write_payload(root: Path, files: dict[str, bytes]) -> None:
    for relative_path, content in files.items():
        path = root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


def test_discovers_torrents_recursively(tmp_path: Path) -> None:
    first = tmp_path / "a.torrent"
    second = tmp_path / "nested" / "b.torrent"
    first.write_bytes(b"")
    second.parent.mkdir()
    second.write_bytes(b"")
    (tmp_path / "ignored.txt").write_text("")

    assert discover_torrent_files(tmp_path) == [first, second]


def test_finds_unique_multifile_root_by_layout_and_size(tmp_path: Path) -> None:
    torrent = parse_torrent_buf(_torrent_buffer("Release", {"video.mkv": b"data"}))
    match = tmp_path / "movies" / "Release"
    wrong = tmp_path / "other" / "Release"
    _write_payload(match, {"video.mkv": b"data"})
    _write_payload(wrong, {"video.mkv": b"wrong-size"})

    assert find_torrent_roots(torrent, tmp_path) == [match]


def test_import_verifies_and_writes_matching_torrent(tmp_path: Path) -> None:
    library = tmp_path / "library"
    source = tmp_path / "torrents"
    target = library / "movies" / "Release"
    source.mkdir()
    files = {"video.mkv": b"data"}
    _write_payload(target, files)
    torrent_path = source / "release.torrent"
    torrent_path.write_bytes(_torrent_buffer("Release", files))

    result = CliRunner().invoke(nx, ["-C", str(library), "import", str(source)])

    assert result.exit_code == 0, result.output
    assert "imported" in result.output
    store = load(target / DefaultStorePathName, ignore_checksum=False)
    entry = store.get_torrent(parse_torrent_buf(torrent_path.read_bytes()).infohash)
    assert entry is not None
    assert entry.nx["@internal"].ready

    repeated = CliRunner().invoke(nx, ["-C", str(library), "import", str(source)])
    assert repeated.exit_code == 0, repeated.output
    assert "existing-ready" in repeated.output


def test_import_dry_run_does_not_write_store(tmp_path: Path) -> None:
    library = tmp_path / "library"
    source = tmp_path / "torrents"
    target = library / "tv-shows" / "Release"
    source.mkdir()
    files = {"episode.mkv": b"episode"}
    _write_payload(target, files)
    (source / "release.torrent").write_bytes(_torrent_buffer("Release", files))

    result = CliRunner().invoke(
        nx, ["-C", str(library), "import", str(source), "--dry-run"]
    )

    assert result.exit_code == 0, result.output
    assert "would-import" in result.output
    assert not (target / DefaultStorePathName).exists()


def test_import_reports_single_file_and_strict_fails(tmp_path: Path) -> None:
    library = tmp_path / "library"
    source = tmp_path / "torrents"
    library.mkdir()
    source.mkdir()
    info = {
        b"name": b"movie.mkv",
        b"piece length": 16 * 1024,
        b"pieces": hashlib.sha1(b"movie").digest(),
        b"length": 5,
    }
    encoded = lt.bencode({b"info": info})
    assert isinstance(encoded, bytes)
    (source / "movie.torrent").write_bytes(encoded)

    result = CliRunner().invoke(
        nx, ["-C", str(library), "import", str(source), "--strict"]
    )

    assert result.exit_code == 1
    assert "single-file" in result.output


def test_import_organizes_single_file_at_any_depth(tmp_path: Path) -> None:
    library = tmp_path / "library"
    source = tmp_path / "torrents"
    payload = library / "foo" / "bar" / "movie.mkv"
    payload.parent.mkdir(parents=True)
    payload.write_bytes(b"movie")
    source.mkdir()
    torrent_path = source / "movie.torrent"
    torrent_path.write_bytes(_single_file_torrent_buffer("movie.mkv", b"movie"))

    result = CliRunner().invoke(
        nx,
        [
            "-C",
            str(library),
            "import",
            str(source),
            "--organize-single-files",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "organized" in result.output
    target = library / "foo" / "bar" / "movie"
    assert not payload.exists()
    assert (target / "movie.mkv").read_bytes() == b"movie"
    store = load(target / DefaultStorePathName, ignore_checksum=False)
    entry = store.get_torrent(parse_torrent_buf(torrent_path.read_bytes()).infohash)
    assert entry is not None
    assert entry.nx["@internal"].strip_components == 0
    assert entry.nx["@internal"].ready

    repeated = CliRunner().invoke(nx, ["-C", str(library), "import", str(source)])
    assert repeated.exit_code == 0, repeated.output
    assert "existing-ready" in repeated.output


def test_import_dry_run_reports_single_file_move(tmp_path: Path) -> None:
    library = tmp_path / "library"
    source = tmp_path / "torrents"
    library.mkdir()
    source.mkdir()
    payload = library / "movie.mkv"
    payload.write_bytes(b"movie")
    (source / "movie.torrent").write_bytes(
        _single_file_torrent_buffer("movie.mkv", b"movie")
    )

    result = CliRunner().invoke(
        nx,
        [
            "-C",
            str(library),
            "import",
            str(source),
            "--organize-single-files",
            "--dry-run",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "would-organize" in result.output
    assert "move movie.mkv -> movie/movie.mkv" in result.output
    assert payload.exists()
    assert not (library / "movie").exists()


def test_import_does_not_move_single_file_when_verification_fails(
    tmp_path: Path,
) -> None:
    library = tmp_path / "library"
    source = tmp_path / "torrents"
    library.mkdir()
    source.mkdir()
    payload = library / "movie.mkv"
    payload.write_bytes(b"wrong")
    (source / "movie.torrent").write_bytes(
        _single_file_torrent_buffer("movie.mkv", b"movie")
    )

    result = CliRunner().invoke(
        nx,
        [
            "-C",
            str(library),
            "import",
            str(source),
            "--organize-single-files",
        ],
    )

    assert result.exit_code == 1
    assert "verification-failed" in result.output
    assert payload.exists()
    assert not (library / "movie").exists()


def test_import_refuses_existing_single_file_target(tmp_path: Path) -> None:
    library = tmp_path / "library"
    source = tmp_path / "torrents"
    library.mkdir()
    source.mkdir()
    (library / "movie.mkv").write_bytes(b"movie")
    (library / "movie").mkdir()
    (source / "movie.torrent").write_bytes(
        _single_file_torrent_buffer("movie.mkv", b"movie")
    )

    result = CliRunner().invoke(
        nx,
        [
            "-C",
            str(library),
            "import",
            str(source),
            "--organize-single-files",
        ],
    )

    assert result.exit_code == 1
    assert "organization target already exists" in result.output
    assert (library / "movie.mkv").exists()


def test_import_requires_explicit_root(tmp_path: Path) -> None:
    result = CliRunner().invoke(nx, ["import", str(tmp_path)])

    assert result.exit_code == 2
    assert "import requires an explicit root" in result.output
