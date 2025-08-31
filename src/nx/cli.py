import base64
import time
from datetime import datetime
from pathlib import Path

import click
import structlog
from rich.console import Console
from rich.tree import Tree

from nx.store import Repo, TorrentEntry

from .nx import Torrent, parse_torrent, parse_torrent_buf

logger: structlog.stdlib.BoundLogger = structlog.get_logger(__name__)


@click.group(invoke_without_command=True)
@click.option("-s", "--store", type=str, default=".nx_store")
@click.option(
    "--max-announce-count",
    type=int,
    default=3,
    help="maximum number of announce urls to show per torrent (0 = show all)",
)
@click.option(
    "--max-files",
    type=int,
    default=26,
    help="maximum number of files to show per torrent (0 = show all)",
)
@click.pass_context
def nx(ctx: click.Context, store: str, max_announce_count: int, max_files: int):
    ctx.ensure_object(dict)
    ctx.obj["store"] = Path(store).absolute()
    ctx.obj["max_announce_count"] = max_announce_count
    ctx.obj["max_files"] = max_files
    Repo.validate_path(ctx.obj["store"])

    if ctx.invoked_subcommand is None:
        _show_entries(ctx.obj["store"], max_announce_count, max_files)


def _show_entries(store_path: Path, max_announce_count: int, max_files: int):
    """Pretty print all entries in the store"""
    try:
        with Repo(store_path) as repo:
            if not repo.store.entries:
                click.echo("no entries found")
                return

            # Extract all entry IDs for prefix calculation
            entry_ids = [entry.id for entry in repo.store.entries]
            prefix_map = _calculate_unique_prefixes(entry_ids)

            for entry in repo.store.entries:
                if isinstance(entry, TorrentEntry):
                    _print_torrent_entry(
                        entry,
                        prefix_map.get(entry.id, ""),
                        max_announce_count,
                        max_files,
                    )
    except FileNotFoundError:
        click.echo("no entries found")


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
    parent_branch, torrent: Torrent, max_announce_count: int = 5, max_files: int = 26
):
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
    entry: TorrentEntry,
    unique_prefix: str = "",
    max_announce_count: int = 5,
    max_files: int = 26,
):
    """Pretty print a torrent entry with hierarchical display"""
    torrent_data = base64.b64decode(entry.torrent)
    torrent = parse_torrent_buf(torrent_data)

    console = Console()

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


def _add_files_to_tree(parent_branch, files: list, max_files: int = 0):
    """Add files to a Rich tree branch with proper directory structure"""
    if not files:
        return

    # Apply file limit if specified
    files_to_show = files
    if max_files > 0:
        files_to_show = files[:max_files]

    # Group files by their directory structure
    tree = {}
    single_files = []

    for file in files_to_show:
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

    # Show "... and N more" if files were truncated
    if 0 < max_files < len(files):
        remaining = len(files) - max_files
        parent_branch.add(f"[dim]... and {remaining} more files[/dim]")


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
        strip_components, root_ref = torrent.auto_strip_root()
        log.info(
            "auto-strip-root", strip_components=strip_components, root_ref=root_ref
        )

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


@nx.command()
@click.argument("identifier", required=False)
@click.option("-a", "--all", "verify_all", is_flag=True, help="verify all torrents")
@click.pass_context
def verify(ctx: click.Context, identifier: str | None, verify_all: bool):
    store: Path = ctx.obj["store"]
    log = logger.bind(
        method="verify", store=store, identifier=identifier, verify_all=verify_all
    )
    log.info("invoked")

    if not identifier and not verify_all:
        click.echo("must specify either an identifier or --all", err=True)
        raise click.Abort()

    if identifier and verify_all:
        click.echo("cannot specify both identifier and --all", err=True)
        raise click.Abort()

    try:
        with Repo(store) as repo:
            if not repo.store.entries:
                click.echo("no entries found")
                return

            if verify_all:
                _verify_all_torrents(repo, store.parent)
            else:
                _verify_torrent_by_id(repo, store.parent, identifier)
    except FileNotFoundError:
        click.echo("no entries found")


def _verify_torrent_by_id(repo: Repo, base_path: Path, identifier: str):
    entry = _find_entry_by_prefix(repo, identifier)
    if entry is None:
        click.echo(f"torrent not found: {identifier}", err=True)
        raise click.Abort()

    _verify_single_torrent(repo, base_path, entry)


def _verify_all_torrents(repo: Repo, base_path: Path):
    torrent_entries = [e for e in repo.store.entries if isinstance(e, TorrentEntry)]
    if not torrent_entries:
        click.echo("no torrent entries found")
        return

    for entry in torrent_entries:
        _verify_single_torrent(repo, base_path, entry)


def _verify_single_torrent(repo: Repo, base_path: Path, entry: TorrentEntry):
    torrent_data = base64.b64decode(entry.torrent)
    torrent = parse_torrent_buf(torrent_data)

    click.echo(f"verifying {entry.id[:8]}...")

    strip_components = entry.nx["@internal"].strip_components
    matches = torrent.matches(base_path, strip_components=strip_components)

    if not matches.ok:
        click.echo(f"files missing or invalid for {entry.id[:8]}", err=True)
        if matches.missing:
            click.echo(f"missing: {len(matches.missing)} files", err=True)
        if matches.error:
            click.echo(f"errors: {len(matches.error)} files", err=True)
        return

    verified = torrent.verify_pieces(base_path, strip_components=strip_components)

    entry.nx["@internal"].last_verified = int(time.time())
    repo.store.upsert(entry)

    if verified:
        click.echo(f"verified {entry.id[:8]} successfully")
        entry.nx["@internal"].ready = True
        repo.store.upsert(entry)
    else:
        click.echo(f"verification failed for {entry.id[:8]}", err=True)


def _find_entry_by_prefix(repo: Repo, identifier: str) -> TorrentEntry | None:
    matches = []
    for entry in repo.store.entries:
        if isinstance(entry, TorrentEntry):
            if entry.id.startswith(identifier.upper()):
                matches.append(entry)

    if len(matches) == 0:
        return None
    elif len(matches) == 1:
        return matches[0]
    else:
        click.echo(f"ambiguous identifier '{identifier}', matches:", err=True)
        for match in matches:
            click.echo(f"  {match.id}", err=True)
        raise click.Abort()


@nx.command()
@click.argument("source")
@click.option(
    "--max-announce-count",
    type=int,
    default=3,
    help="maximum number of announce urls to show per torrent (0 = show all)",
)
@click.option(
    "--max-files",
    type=int,
    default=26,
    help="maximum number of files to show per torrent (0 = show all)",
)
def parse(source: str, max_announce_count: int, max_files: int):
    """Parse a torrent file and display its info"""
    source_path = Path(source).expanduser()
    if not source_path.exists():
        click.echo(f"source does not exist: '{source}'", err=True)
        raise click.Abort()

    try:
        torrent = parse_torrent(source_path)
        _print_torrent_info(torrent, max_announce_count, max_files)
    except Exception as e:
        click.echo(f"failed to parse torrent: {e}", err=True)
        raise click.Abort()


def _print_torrent_info(
    torrent: Torrent, max_announce_count: int = 5, max_files: int = 26
):
    """Print torrent info without store metadata"""
    console = Console()

    tree = Tree(f"[green]torrent:[/green] {torrent.info.name()}")

    # Basic info
    tree.add(f"[cyan]infohash:[/cyan] {torrent.infohash}")
    tree.add(f"[cyan]size:[/cyan] {_format_size(torrent.sz)}")
    tree.add(f"[cyan]pieces:[/cyan] {torrent.pc}")

    # Add common torrent info
    _add_torrent_info_to_tree(tree, torrent, max_announce_count, max_files)

    console.print(tree)


if __name__ == "__main__":
    nx()
