import hashlib
from pathlib import Path

import libtorrent as lt
from rich.console import Console
from rich.tree import Tree

from nx.cli_helpers import _print_torrent_info
from nx.cli_helpers_tree import _add_files_to_tree
from nx.nx import File, parse_torrent_buf


def test_directory_size_includes_files_omitted_by_display_limit() -> None:
    files = [
        File(offset=0, size=7, path=Path("Release/visible.mkv")),
        File(offset=7, size=13, path=Path("Release/.hidden.mkv")),
    ]
    tree = Tree("torrent")

    _add_files_to_tree(tree, files, max_files=1)

    console = Console(record=True, color_system=None)
    console.print(tree)
    output = console.export_text()
    assert "Release/ (20 B)" in output
    assert "visible.mkv (7 B)" in output
    assert ".hidden.mkv" not in output
    assert "... and 1 more files" in output


def test_torrent_tree_escapes_bracketed_names() -> None:
    content = b"data"
    encoded = lt.bencode(
        {
            b"info": {
                b"name": b"Release[rarbg]",
                b"piece length": 16 * 1024,
                b"pieces": hashlib.sha1(content).digest(),
                b"files": [{b"length": len(content), b"path": [b"video[1080p].mkv"]}],
            }
        }
    )
    assert isinstance(encoded, bytes)
    torrent = parse_torrent_buf(encoded)
    console = Console(record=True, color_system=None)

    _print_torrent_info(console, torrent)

    output = console.export_text()
    assert "torrent: Release[rarbg]" in output
    assert "Release[rarbg]/" in output
    assert "video[1080p].mkv" in output
