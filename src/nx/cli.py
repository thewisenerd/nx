import os
import time
from pathlib import Path
from typing import Optional

import click
import structlog
from rich.console import Console

from .cli_helpers import (
    _calculate_unique_prefixes,
    _print_torrent_entry,
    _print_torrent_info,
)
from .click_pathtype import PathType
from .nx import Torrent, parse_torrent, parse_torrent_buf
from .store import DefaultStorePathName, Repo, TorrentEntry

logger: structlog.stdlib.BoundLogger = structlog.get_logger(__name__)


_default_store_path = Path(DefaultStorePathName).absolute()

console = Console()

ctx_keys = {
    "store_path": "store_path",
    "max_announce_count": "max_announce_count",
    "max_files": "max_files",
}


def _get_store_path(ctx: click.Context) -> Path | None:
    store: Path | None = ctx.obj.get(ctx_keys["store_path"])
    return store


@click.group(invoke_without_command=True)
@click.option("-s", "--store", type=Optional[str], help="use a specific store file")
@click.option(
    "--max-announce-count",
    type=click.IntRange(min=0),
    default=3,
    help="maximum number of announce urls to show per torrent (0 = show all)",
)
@click.option(
    "--max-files",
    type=click.IntRange(min=0),
    default=26,
    help="maximum number of files to show per torrent (0 = show all)",
)
@click.pass_context
def nx(
    ctx: click.Context, store: str | None, max_announce_count: int, max_files: int
) -> None:
    ctx.ensure_object(dict)
    ctx.obj[ctx_keys["store_path"]] = Path(store).absolute() if store else None
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


def _resolve_root(
    torrent: Torrent, store_path: Path | None, root_ref: str
) -> Path | None:
    log = logger.bind(method="_resolve_root", id=torrent.infohash, root_ref=root_ref)

    search = store_path.parent if store_path else _default_store_path.parent
    log.info("invoked", search=search)

    # candidate 1, we are "in" the 'root_ref' directory
    if search.parts[-1] == root_ref:
        log.info("in root-ref")
        return store_path

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

    # candidate 3, we need a root ref, but it doesn't exist yet..
    if candidate.parent.exists() and candidate.parent.is_dir():
        console.print("creating directory ", end="")
        console.print(
            candidate.name, style="yellow", markup=False, highlight=False, end=""
        )
        console.print("")

        candidate.mkdir(exist_ok=True)
        os.chdir(candidate)

        new_store_path = candidate / DefaultStorePathName
        log.info("creating root-ref", new_store_path=new_store_path)
        return new_store_path

    return store_path


@nx.command(help="add a torrent file to the store")
@click.argument("source", type=PathType(allowed_extensions={".torrent"}))
@click.option(
    "--strip-components",
    type=Optional[int],
    help="number of path components to strip when adding files",
)
@click.option(
    "--auto-strip-root/--no-auto-strip-root",
    default=True,
    help="automatically strip root directory and resolve store path",
)
@click.pass_context
def add(
    ctx: click.Context,
    source: Path,
    strip_components: int | None,
    auto_strip_root: bool,
) -> None:
    store_path: Path | None = _get_store_path(ctx)
    log = logger.bind(
        method="add",
        store=store_path,
        source=source,
        strip_components=strip_components,
        auto_strip_root=auto_strip_root,
    )
    log.info("invoked")

    if not source.exists():
        click.echo(f"source does not exist: '{source}'", err=True)
        raise click.Abort()
    if not source.is_file():
        click.echo(f"source is not a file: '{source}'", err=True)
        raise click.Abort()

    torrent: Torrent = parse_torrent(source)

    if auto_strip_root:
        if strip_components is not None:
            click.echo("cannot use --strip-components with --auto-strip-root", err=True)
            raise click.Abort()

        root_ref = torrent.strip_root()
        log.info("auto-strip-root", root_ref=root_ref)

        if root_ref:
            strip_components = 1
            store_path = _resolve_root(torrent, store_path, root_ref)
        else:
            # TODO: do not allow torrents without a root-ref unless -f specified
            pass

    entry = TorrentEntry.from_torrent(torrent, strip_components or 0)

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
@click.option("-a", "--all", "verify_all", is_flag=True, help="verify all torrents")
@click.pass_context
def verify(ctx: click.Context, identifier: str | None, verify_all: bool) -> None:
    """verify the files for a torrent by its identifier (prefix)"""
    store_path: Path | None = _get_store_path(ctx)
    log = logger.bind(
        method="verify", store=store_path, identifier=identifier, verify_all=verify_all
    )
    log.info("invoked")

    if not identifier and not verify_all:
        click.echo("must specify either an identifier or --all", err=True)
        raise click.Abort()

    if identifier and verify_all:
        click.echo("cannot specify both identifier and --all", err=True)
        raise click.Abort()

    resolved_store_path: Path = store_path if store_path else _default_store_path
    with Repo(resolved_store_path) as repo:
        if not repo.store.entries:
            click.echo("no entries found")
            return

        if verify_all:
            _verify_all_torrents(repo)
        else:
            _verify_torrent_by_id(repo, identifier or "")


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
def parse(source: str, max_announce_count: int, max_files: int) -> None:
    """parse a torrent file and display its info"""
    source_path = Path(source).expanduser()
    if not source_path.exists():
        click.echo(f"source does not exist: '{source}'", err=True)
        raise click.Abort()

    torrent = parse_torrent(source_path)
    _print_torrent_info(console, torrent, max_announce_count, max_files)


if __name__ == "__main__":
    nx()
