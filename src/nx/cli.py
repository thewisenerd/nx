import time
import typing as t
from pathlib import Path

import click
import structlog

from nx.store import Repo, TorrentEntry

from .cli_helpers import (
    _calculate_unique_prefixes,
    _print_torrent_entry,
    _print_torrent_info,
)
from .nx import Torrent, parse_torrent, parse_torrent_buf

logger: structlog.stdlib.BoundLogger = structlog.get_logger(__name__)


_default_store_path = Path(".nx_store").absolute()

ctx_keys = {
    "store_path": "store_path",
    "max_announce_count": "max_announce_count",
    "max_files": "max_files",
}


def _get_store_path(ctx: click.Context) -> Path | None:
    store: Path | None = ctx.obj.get(ctx_keys["store_path"])
    return store


@click.group(invoke_without_command=True)
@click.option("-s", "--store", type=t.Optional[str])
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
def nx(ctx: click.Context, store: str | None, max_announce_count: int, max_files: int):
    ctx.ensure_object(dict)
    ctx.obj[ctx_keys["store_path"]] = Path(store).absolute() if store else None
    ctx.obj[ctx_keys["max_announce_count"]] = max_announce_count
    ctx.obj[ctx_keys["max_files"]] = max_files

    store_path = _get_store_path(ctx)
    if store:
        Repo.validate_path(store_path)

    if ctx.invoked_subcommand is None:
        _show_entries(_get_store_path(ctx), max_announce_count, max_files)


def _show_entries(store_path: Path | None, max_announce_count: int, max_files: int):
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
                    entry,
                    prefix_map.get(entry.id, ""),
                    max_announce_count,
                    max_files,
                )


@nx.command()
@click.argument("source")
@click.option("--strip-components", type=int, required=False)
@click.option("--auto-strip-root/--no-auto-strip-root", default=True)
@click.pass_context
def add(
    ctx: click.Context, source: str, strip_components: int | None, auto_strip_root: bool
):
    store_path: Path | None = _get_store_path(ctx)
    log = logger.bind(
        method="add",
        store=store_path,
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

    store_path: Path = store_path if store_path else _default_store_path
    with Repo(store_path) as repo:
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
        matches = torrent.matches(repo.save_path, strip_components=strip_components)
        if matches.ok:
            verified = torrent.verify_pieces(
                repo.save_path, strip_components=strip_components
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

    store_path: Path = store_path if store_path else _default_store_path
    with Repo() as repo:
        if not repo.store.entries:
            click.echo("no entries found")
            return

        if verify_all:
            _verify_all_torrents(repo)
        else:
            _verify_torrent_by_id(repo, identifier)


def _verify_torrent_by_id(repo: Repo, identifier: str):
    entry = _find_entry_by_prefix(repo, identifier)
    if entry is None:
        click.echo(f"torrent not found: {identifier}", err=True)
        raise click.Abort()

    _verify_single_torrent(repo, entry)


def _verify_all_torrents(repo: Repo):
    torrent_entries = [e for e in repo.store.entries if isinstance(e, TorrentEntry)]
    if not torrent_entries:
        click.echo("no torrent entries found")
        return

    for entry in torrent_entries:
        _verify_single_torrent(repo, entry)


def _verify_single_torrent(repo: Repo, entry: TorrentEntry):
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
def parse(source: str, max_announce_count: int, max_files: int):
    """Parse a torrent file and display its info"""
    source_path = Path(source).expanduser()
    if not source_path.exists():
        click.echo(f"source does not exist: '{source}'", err=True)
        raise click.Abort()

    torrent = parse_torrent(source_path)
    _print_torrent_info(torrent, max_announce_count, max_files)


if __name__ == "__main__":
    nx()
