import time
import base64
from datetime import datetime
from pathlib import Path

import click
import structlog
from rich.console import Console
from rich.tree import Tree

from nx.store import TorrentEntry, Repo

from .nx import parse_torrent, parse_torrent_buf, Torrent

logger: structlog.stdlib.BoundLogger = structlog.get_logger(__name__)


@click.group(invoke_without_command=True)
@click.option("-s", "--store", type=str, default=".nx_store")
@click.pass_context
def nx(ctx: click.Context, store: str):
    ctx.ensure_object(dict)
    ctx.obj["store"] = Path(store).absolute()
    Repo.validate_path(ctx.obj["store"])

    if ctx.invoked_subcommand is None:
        _show_entries(ctx.obj["store"])


def _show_entries(store_path: Path):
    """Pretty print all entries in the store"""
    try:
        with Repo(store_path) as repo:
            if not repo.store.entries:
                click.echo("no entries found")
                return

            for entry in repo.store.entries:
                if isinstance(entry, TorrentEntry):
                    _print_torrent_entry(entry)
    except FileNotFoundError:
        click.echo("no entries found")


def _print_torrent_entry(entry: TorrentEntry):
    """Pretty print a torrent entry with hierarchical display"""
    torrent_data = base64.b64decode(entry.torrent)
    torrent = parse_torrent_buf(torrent_data)

    console = Console()

    # Root tree with entry ID
    tree = Tree(f"[bold cyan]{entry.id}[/bold cyan]")

    # Torrent section
    torrent_branch = tree.add(f"[green]torrent:[/green] {torrent.info.name()}")

    # Announce section
    if torrent.trackers:
        announce_branch = torrent_branch.add("[yellow]announce[/yellow]")
        for tracker in torrent.trackers:
            announce_branch.add(tracker.url)

    # Files structure
    _add_files_to_tree(torrent_branch, torrent.files)

    # Private flag
    private_status = "true" if torrent.private else "false"
    torrent_branch.add(f"[magenta]private:[/magenta] {private_status}")

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


def _add_files_to_tree(parent_branch, files: list):
    """Add files to a Rich tree branch with proper directory structure"""
    if not files:
        return

    # Group files by their directory structure
    tree = {}
    single_files = []

    for file in files:
        if len(file.path.parts) == 1:
            single_files.append(file)
        else:
            current = tree
            for part in file.path.parts[:-1]:
                if part not in current:
                    current[part] = {}
                current = current[part]

            # Store the file at the leaf
            if "_files" not in current:
                current["_files"] = []
            current["_files"].append((file.path.parts[-1], file))

    # Add single files first
    for file in single_files:
        parent_branch.add(f"{file.path.name} ({_format_size(file.size)})")

    # Add directories
    for dir_name in sorted(tree.keys()):
        dir_size = _calculate_dir_size(tree[dir_name])
        dir_branch = parent_branch.add(f"{dir_name}/ ({_format_size(dir_size)})")
        _add_dir_contents_to_tree(dir_branch, tree[dir_name])


def _add_dir_contents_to_tree(parent_branch, tree_node: dict):
    """Add directory contents to tree branch"""
    items = []

    # Add files
    if "_files" in tree_node:
        for filename, file in tree_node["_files"]:
            items.append(("file", filename, file))

    # Add subdirectories
    for key, subtree in tree_node.items():
        if key != "_files":
            items.append(("dir", key, subtree))

    # Sort items - dirs first, then files
    items.sort(key=lambda x: (x[0] != "dir", x[1]))

    for item_type, name, data in items:
        if item_type == "file":
            parent_branch.add(f"{name} ({_format_size(data.size)})")
        else:
            dir_size = _calculate_dir_size(data)
            dir_branch = parent_branch.add(f"{name}/ ({_format_size(dir_size)})")
            _add_dir_contents_to_tree(dir_branch, data)


def _format_size(size_bytes: int) -> str:
    """Format size in human readable format"""
    if size_bytes == 0:
        return "0 B"

    units = ["B", "KB", "MB", "GB", "TB"]
    size = float(size_bytes)
    unit_idx = 0

    while size >= 1024 and unit_idx < len(units) - 1:
        size /= 1024
        unit_idx += 1

    if size == int(size):
        return f"{int(size)} {units[unit_idx]}"
    return f"{size:.2f} {units[unit_idx]}"


def _calculate_dir_size(tree_node: dict) -> int:
    """Calculate total size of directory"""
    total = 0

    if "_files" in tree_node:
        for _, file in tree_node["_files"]:
            total += file.size

    for key, subtree in tree_node.items():
        if key != "_files":
            total += _calculate_dir_size(subtree)

    return total


@nx.command()
@click.argument("source")
@click.option("--strip-components", type=int, required=False)
@click.option("--auto-strip-root/--no-auto-strip-root", default=True)
@click.pass_context
def add(
    ctx: click.Context, source: str, strip_components: int | None, auto_strip_root: bool
):
    store: Path = ctx.obj["store"]
    log = logger.bind(
        method="add",
        store=store,
        source=source,
        strip_components=strip_components,
        auto_strip_root=auto_strip_root,
    )
    log.info("invoked")

    source = Path(source).expanduser()
    if not source.exists():
        click.echo(f"source does not exist: '{source}'", err=True)
        raise click.Abort()

    torrent: Torrent = parse_torrent(source)

    if auto_strip_root:
        if strip_components is not None:
            click.echo("cannot use --strip-components with --auto-strip-root", err=True)
            raise click.Abort()
        strip_components = torrent.auto_strip_root()
        log.info("auto-strip-root", strip_components=strip_components)

    entry = TorrentEntry.from_torrent(torrent, strip_components)

    with Repo(store) as repo:
        existing = repo.store.get_torrent(entry.id)
        if existing is not None:
            existing_meta = existing.nx["@internal"]
            if existing_meta.strip_components != entry.nx["@internal"].strip_components:
                click.echo(
                    f"torrent already exists with different strip_components (existing={existing_meta.strip_components}, new={entry.nx['@internal'].strip_components}): {entry.id}",
                    err=True,
                )
                raise click.Abort()

            if existing_meta.ready:
                click.echo(f"torrent already exists and is ready: {entry.id}", err=True)
                raise click.Abort()

        log.info("adding new torrent", id=entry.id)
        matches = torrent.matches(store.parent, strip_components=strip_components)
        if matches.ok:
            verified = torrent.verify_pieces(
                store.parent, strip_components=strip_components
            )
            log.info("verified", verified=verified)
            entry.nx["@internal"].ready = verified
            entry.nx["@internal"].last_verified = int(time.time())

        repo.store.upsert(entry)
        click.echo(f"added torrent: {torrent.infohash}")


if __name__ == "__main__":
    nx()
