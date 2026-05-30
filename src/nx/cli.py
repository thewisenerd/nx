import time
import urllib.parse
from collections.abc import Callable, Iterator
from pathlib import Path

import click
import httpx
import structlog
from rich.console import Console

from .cli_helpers import (
    _calculate_unique_prefixes,
    _print_torrent_entry,
    _print_torrent_info,
)
from .click_pathtype import PathType
from .config import cache_dir, parse_config
from .nx import Torrent, parse_torrent, parse_torrent_buf
from .store import DefaultStorePathName, Repo, TorrentEntry, load

logger: structlog.stdlib.BoundLogger = structlog.get_logger(__name__)


_default_store_path = Path(DefaultStorePathName).absolute()

console = Console()

ctx_keys = {
    "root_path": "root_path",
    "store_path": "store_path",
    "max_announce_count": "max_announce_count",
    "max_files": "max_files",
}


def _get_root_path(ctx: click.Context) -> Path:
    root: Path = ctx.obj[ctx_keys["root_path"]]
    return root


def _get_store_path(ctx: click.Context) -> Path | None:
    store: Path | None = ctx.obj.get(ctx_keys["store_path"])
    return store


@click.group(invoke_without_command=True)
@click.option(
    "-C",
    "--root",
    type=str,
    default=None,
    metavar="PATH",
    help="operate in this directory",
)
@click.option(
    "--max-announce-count",
    type=click.IntRange(min=0),
    default=3,
    show_default=True,
    help="maximum number of announce urls to show per torrent (0 = show all)",
)
@click.option(
    "--max-files",
    type=click.IntRange(min=0),
    default=26,
    show_default=True,
    help="maximum number of files to show per torrent (0 = show all)",
)
@click.pass_context
def nx(
    ctx: click.Context, root: str | None, max_announce_count: int, max_files: int
) -> None:
    ctx.ensure_object(dict)
    root_path = Path(root).absolute() if root else Path.cwd()
    ctx.obj[ctx_keys["root_path"]] = root_path
    ctx.obj[ctx_keys["store_path"]] = root_path / DefaultStorePathName if root else None
    ctx.obj[ctx_keys["max_announce_count"]] = max_announce_count
    ctx.obj[ctx_keys["max_files"]] = max_files

    store_path = _get_store_path(ctx)
    if store_path:
        Repo.validate_path(store_path)

    if ctx.invoked_subcommand is None:
        _show_entries(_get_store_path(ctx), max_announce_count, max_files)


def _show_entries(
    store_path: Path | None, max_announce_count: int, max_files: int
) -> None:
    """Pretty print all entries in the store"""
    with Repo(store_path if store_path else _default_store_path) as repo:
        if not repo.store.entries:
            click.echo("no entries found")
            return

        # Extract all entry IDs for prefix calculation
        entry_ids = [entry.id for entry in repo.store.entries]
        prefix_map = _calculate_unique_prefixes(entry_ids)

        for entry in repo.store.entries:
            if isinstance(entry, TorrentEntry):
                _print_torrent_entry(
                    console,
                    entry,
                    prefix_map.get(entry.id, ""),
                    max_announce_count,
                    max_files,
                )


def _resolve_root(torrent: Torrent, store_path: Path | None, root_ref: str) -> Path:
    log = logger.bind(method="_resolve_root", id=torrent.infohash, root_ref=root_ref)

    search = store_path.parent if store_path else _default_store_path.parent
    log.info("invoked", search=search)

    # case 1: we are "in" the root_ref directory
    if search.parts[-1] == root_ref:
        log.info("in root-ref")
        return store_path if store_path else _default_store_path

    # case 2: we are "above" the root_ref directory
    candidate = search / root_ref
    if candidate.exists():
        if not candidate.is_dir():
            click.echo(
                f"root-ref exists but is not a directory: '{candidate}'", err=True
            )
            raise click.Abort()

        new_store_path = candidate / DefaultStorePathName
        log.info("above root-ref", new_store_path=new_store_path)

        console.print("switching to directory ", end="")
        console.print(
            candidate.name, style="yellow", markup=False, highlight=False, end=""
        )
        console.print("")

        return new_store_path

    # case 3: root_ref directory doesn't exist, create it
    if candidate.parent.exists() and candidate.parent.is_dir():
        console.print("creating directory ", end="")
        console.print(
            candidate.name, style="yellow", markup=False, highlight=False, end=""
        )
        console.print("")

        candidate.mkdir(exist_ok=True)

        new_store_path = candidate / DefaultStorePathName
        log.info("creating root-ref", new_store_path=new_store_path)
        return new_store_path

    click.echo(f"cannot resolve root directory: '{candidate}'", err=True)
    raise click.Abort()


def _iter_dirs_at_depth(root: Path, depth: int) -> Iterator[Path]:
    directories = [root]

    for _ in range(depth):
        directories = [
            child
            for directory in directories
            for child in sorted(directory.iterdir())
            if child.is_dir()
        ]

    yield from directories


def _is_valid_store(store_path: Path) -> bool:
    try:
        load(store_path, ignore_checksum=False)
        return True
    except Exception:
        return False


def _iter_nested_store_roots(root: Path) -> Iterator[Path]:
    for store_path in sorted(root.rglob(DefaultStorePathName)):
        if store_path.parent == root:
            continue
        if _is_valid_store(store_path):
            yield store_path.parent


def _create_lint_path_formatter(root: Path) -> Callable[[Path], Path]:
    if root.resolve() != Path.cwd().resolve():
        return lambda path: path

    def format_relative_to_root(path: Path) -> Path:
        relative_path = path.relative_to(root)
        if not relative_path.parts:
            return Path(".")
        return relative_path

    return format_relative_to_root


@nx.command(help="lint directories for missing or unverified stores")
@click.option(
    "-d",
    "--depth",
    type=click.IntRange(min=1),
    default=1,
    show_default=True,
    help="directory depth to lint",
)
@click.pass_context
def lint(ctx: click.Context, depth: int) -> None:
    root = _get_root_path(ctx)
    format_path = _create_lint_path_formatter(root)
    found_errors = False

    for directory in _iter_dirs_at_depth(root, depth):
        store_path = directory / DefaultStorePathName
        if not store_path.exists():
            display_path = format_path(directory)
            click.echo(f"{display_path}: error: missing-store: missing .nx_store")
            found_errors = True
            continue

        store = load(store_path, ignore_checksum=False)
        unverified_count = sum(
            1
            for entry in store.entries
            if isinstance(entry, TorrentEntry) and not entry.nx["@internal"].ready
        )
        if unverified_count > 0:
            noun = "entry" if unverified_count == 1 else "entries"
            display_path = format_path(directory)
            click.echo(
                f"{display_path}: error: unverified: {unverified_count} torrent {noun} not verified"
            )
            found_errors = True

        for nested_root in _iter_nested_store_roots(directory):
            display_path = format_path(nested_root)
            display_parent = format_path(directory)
            click.echo(
                f"{display_path}: error: nested-store: valid .nx_store nested under {display_parent}"
            )
            found_errors = True

    if found_errors:
        ctx.exit(1)


_max_torrent_download_size = 16 * 1024 * 1024


def _download_torrent_url(url: str, /, label: str) -> bytes:
    config = parse_config()

    logger.info("downloading torrent", url=url, proxy=config.proxy)

    buffer = bytearray()
    try:
        with httpx.stream(
            "GET", url, proxy=config.proxy, follow_redirects=True
        ) as response:
            if response.status_code != 200:
                click.echo(
                    f"failed to download torrent for {label}: status_code={response.status_code}",
                    err=True,
                )
                raise click.Abort()

            for chunk in response.iter_bytes():
                if not chunk:
                    continue

                if not buffer and chunk[0] != ord("d"):
                    click.echo(f"invalid torrent file downloaded for {label}", err=True)
                    raise click.Abort()

                buffer.extend(chunk)
                if len(buffer) > _max_torrent_download_size:
                    click.echo(
                        f"torrent file too large for {label}: max_size={_max_torrent_download_size}",
                        err=True,
                    )
                    raise click.Abort()
    except httpx.HTTPError as error:
        click.echo(f"failed to download torrent for {label}: {error}", err=True)
        raise click.Abort() from error

    if not buffer:
        click.echo(f"empty torrent file downloaded for {label}", err=True)
        raise click.Abort()

    return bytes(buffer)


def _download_magnet(infohash: str) -> bytes:
    return _download_torrent_url(
        f"https://itorrents.net/torrent/{infohash}.torrent",
        label=f"magnet link: {infohash}",
    )


def _parse_magnet(parsed: urllib.parse.ParseResult) -> str:
    params = urllib.parse.parse_qs(parsed.query)
    xt = params.get("xt", [])
    if not xt:
        click.echo("magnet link missing 'xt' parameter", err=True)
        raise click.Abort()
    parts = xt[0].split(":")
    if len(parts) != 3 or parts[0] != "urn" or parts[1] != "btih":
        click.echo("magnet link 'xt' parameter is not a valid btih urn", err=True)
        raise click.Abort()
    infohash = parts[2].upper()

    if len(infohash) != 40:
        click.echo("magnet link 'xt' infohash is not a valid SHA1 hash", err=True)
        raise click.Abort()

    return infohash


def _parse_torrent(source: str) -> Torrent:
    log = logger.bind(method="_parse_torrent", source=source)
    parsed = urllib.parse.urlparse(source)

    torrent: Torrent
    if parsed.scheme == "magnet":
        infohash = _parse_magnet(parsed)

        source_path = cache_dir / f"{infohash}.torrent"

        if not source_path.exists():
            torrent_bytes = _download_magnet(infohash)
            torrent = parse_torrent_buf(torrent_bytes)

            # parsing success, write to cache now
            source_path.parent.mkdir(parents=True, exist_ok=True)
            source_path.write_bytes(torrent_bytes)
        else:
            torrent = parse_torrent(source_path.read_bytes())
    elif parsed.scheme in {"http", "https"}:
        torrent_bytes = _download_torrent_url(source, label=source)
        torrent = parse_torrent_buf(torrent_bytes)
    else:
        if parsed.scheme == "file":
            log.debug(
                "identified file:// scheme", netloc=parsed.netloc, path=parsed.path
            )
            source_path = Path(urllib.parse.unquote(parsed.path))
        else:
            source_path = Path(parsed.path).expanduser()

        if not source_path.exists():
            click.echo(f"source does not exist: '{source}'", err=True)
            raise click.Abort()
        if not source_path.is_file():
            click.echo(f"source is not a file: '{source}'", err=True)
            raise click.Abort()

        torrent = parse_torrent(source_path.read_bytes())

    return torrent


@nx.command(help="add a torrent file to the store")
@click.argument("source", type=PathType(allowed_extensions={".torrent"}))
@click.option(
    "-f",
    "--here",
    is_flag=True,
    help="use current directory as root",
)
@click.pass_context
def add(
    ctx: click.Context,
    source: str,
    here: bool,
) -> None:
    store_path: Path | None = _get_store_path(ctx)
    log = logger.bind(
        method="add",
        store=store_path,
        source=source,
        here=here,
    )
    log.info("invoked")

    torrent = _parse_torrent(source)

    # determine strip_components from torrent structure
    root_ref = torrent.strip_root()
    is_multi_file = root_ref is not None
    strip_components = 1 if is_multi_file else 0

    if here:
        # user says "trust me, store goes here"
        log.info("using current directory as root", is_multi_file=is_multi_file)
        if store_path is None:
            store_path = _default_store_path
    else:
        # auto-detect: try to resolve root directory
        if is_multi_file:
            assert root_ref is not None
            store_path = _resolve_root(torrent, store_path, root_ref)
        else:
            click.echo(
                "single-file torrent requires -f/--here to specify store location",
                err=True,
            )
            raise click.Abort()

    entry = TorrentEntry.from_torrent(torrent, strip_components)

    resolved_store_path: Path = store_path if store_path else _default_store_path
    with Repo(resolved_store_path) as repo:
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
        matches = torrent.matches(
            repo.save_path, strip_components=strip_components or 0
        )
        if matches.ok:
            verified = torrent.verify_pieces(
                repo.save_path, strip_components=strip_components or 0
            )
            log.info("verified", verified=verified)
            entry.nx["@internal"].ready = verified
            entry.nx["@internal"].last_verified = int(time.time())

        repo.store.upsert(entry)
        click.echo(f"added torrent: {torrent.infohash}")


@nx.command()
@click.argument("identifier", required=False)
@click.pass_context
def verify(ctx: click.Context, identifier: str | None) -> None:
    """verify torrents (all if no identifier given)"""
    store_path: Path | None = _get_store_path(ctx)
    log = logger.bind(method="verify", store=store_path, identifier=identifier)
    log.info("invoked")

    resolved_store_path: Path = store_path if store_path else _default_store_path
    with Repo(resolved_store_path) as repo:
        if not repo.store.entries:
            click.echo("no entries found")
            return

        if identifier:
            _verify_torrent_by_id(repo, identifier)
        else:
            _verify_all_torrents(repo)


def _verify_torrent_by_id(repo: Repo, identifier: str) -> None:
    entry = _find_entry_by_prefix(repo, identifier)
    if entry is None:
        click.echo(f"torrent not found: {identifier}", err=True)
        raise click.Abort()

    _verify_single_torrent(repo, entry)


def _verify_all_torrents(repo: Repo) -> None:
    torrent_entries = [e for e in repo.store.entries if isinstance(e, TorrentEntry)]
    if not torrent_entries:
        click.echo("no torrent entries found")
        return

    for entry in torrent_entries:
        _verify_single_torrent(repo, entry)


def _verify_single_torrent(repo: Repo, entry: TorrentEntry) -> None:
    torrent = parse_torrent_buf(entry.buffer())

    click.echo(f"verifying {entry.id[:8]}...")

    strip_components = entry.nx["@internal"].strip_components

    matches = torrent.matches(repo.save_path, strip_components=strip_components)

    if not matches.ok:
        click.echo(f"files missing or invalid for {entry.id[:8]}", err=True)
        if matches.missing:
            click.echo(f"missing: {len(matches.missing)} files", err=True)
            for path in matches.missing:
                click.echo(f"  {path}", err=True)
        if matches.error:
            click.echo(f"errors: {len(matches.error)} files", err=True)
        return

    verified = torrent.verify_pieces(repo.save_path, strip_components=strip_components)

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


@nx.command(help="parse a torrent file and display its info")
@click.argument("source", type=PathType(allowed_extensions={".torrent"}))
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
def parse(source: str, max_announce_count: int, max_files: int) -> None:
    """parse a torrent file and display its info"""
    torrent = _parse_torrent(source)
    _print_torrent_info(console, torrent, max_announce_count, max_files)


if __name__ == "__main__":
    nx()
