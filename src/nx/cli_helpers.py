import base64
from datetime import datetime

from rich.console import Console
from rich.tree import Tree

from .cli_helpers_tree import _add_files_to_tree, _format_size
from .nx import Torrent, parse_torrent_buf
from .store import TorrentEntry


def _calculate_unique_prefixes(ids: list[str]) -> dict[str, str]:
    """Calculate shortest unique prefix for each individual ID"""
    if not ids:
        return {}

    prefix_map = {}

    for id_str in ids:
        # Find the shortest prefix for this specific ID
        for prefix_len in range(1, len(id_str) + 1):
            prefix = id_str[:prefix_len]
            # Check if this prefix uniquely identifies this ID among all others
            conflicts = [
                other_id
                for other_id in ids
                if other_id != id_str and other_id.startswith(prefix)
            ]
            if not conflicts:
                prefix_map[id_str] = prefix
                break

        # Fallback to full ID if no unique prefix found
        if id_str not in prefix_map:
            prefix_map[id_str] = id_str

    return prefix_map


def _add_torrent_info_to_tree(
    parent_branch: Tree,
    torrent: Torrent,
    max_announce_count: int,
    max_files: int,
) -> None:
    """Add common torrent information to a tree branch"""
    # Announce section
    if torrent.trackers:
        announce_branch = parent_branch.add("[yellow]announce[/yellow]")

        trackers_to_show = torrent.trackers
        if max_announce_count > 0:
            trackers_to_show = torrent.trackers[:max_announce_count]

        for tracker in trackers_to_show:
            announce_branch.add(tracker.url)

        if 0 < max_announce_count < len(torrent.trackers):
            remaining = len(torrent.trackers) - max_announce_count
            announce_branch.add(f"[dim]... and {remaining} more[/dim]")

    # Files structure
    _add_files_to_tree(parent_branch, torrent.files, max_files)

    # Private flag
    private_status = "true" if torrent.private else "false"
    parent_branch.add(f"[magenta]private:[/magenta] {private_status}")


def _print_torrent_entry(
    console: Console,
    entry: TorrentEntry,
    unique_prefix: str = "",
    max_announce_count: int = 5,
    max_files: int = 26,
) -> None:
    """Pretty print a torrent entry with hierarchical display"""
    torrent_data = base64.b64decode(entry.torrent)
    torrent = parse_torrent_buf(torrent_data)

    # Root tree with entry ID - highlight unique prefix
    if unique_prefix and len(unique_prefix) < len(entry.id):
        remaining = entry.id[len(unique_prefix) :]
        tree = Tree(
            f"[bright_yellow]{unique_prefix}[/bright_yellow][dim grey]{remaining}[/dim grey]"
        )
    else:
        tree = Tree(f"[bold grey]{entry.id}[/bold grey]")

    # Torrent section
    torrent_branch = tree.add(f"[green]torrent:[/green] {torrent.info.name()}")

    # Add common torrent info
    _add_torrent_info_to_tree(torrent_branch, torrent, max_announce_count, max_files)

    # NX metadata section
    nx_branch = tree.add("[blue]nx[/blue]")
    internal_branch = nx_branch.add("[blue]@internal[/blue]")

    internal = entry.nx["@internal"]

    internal_branch.add(f"strip-components: {internal.strip_components}")
    internal_branch.add(f"ready: {str(internal.ready).lower()}")

    if internal.last_verified is not None:
        timestamp = datetime.fromtimestamp(internal.last_verified).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        internal_branch.add(f"last-verified: {timestamp}")

    console.print(tree)


def _print_torrent_info(
    console: Console, torrent: Torrent, max_announce_count: int = 5, max_files: int = 26
) -> None:
    """Print torrent info without store metadata"""
    tree = Tree(f"[green]torrent:[/green] {torrent.info.name()}")

    # Basic info
    tree.add(f"[cyan]infohash:[/cyan] {torrent.infohash}")
    tree.add(f"[cyan]size:[/cyan] {_format_size(torrent.sz)}")
    tree.add(f"[cyan]pieces:[/cyan] {torrent.pc}")

    # Add common torrent info
    _add_torrent_info_to_tree(tree, torrent, max_announce_count, max_files)

    console.print(tree)
