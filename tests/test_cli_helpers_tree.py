from pathlib import Path

from rich.console import Console
from rich.tree import Tree

from nx.cli_helpers_tree import _add_files_to_tree
from nx.nx import File


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
