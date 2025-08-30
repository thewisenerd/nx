import time
from pathlib import Path

import click
import structlog

from nx.store import TorrentEntry, Repo

from .nx import parse_torrent, Torrent

logger: structlog.stdlib.BoundLogger = structlog.get_logger(__name__)


@click.group()
@click.option("-s", "--store", type=str, default=".nx_store")
@click.pass_context
def nx(ctx: click.Context, store: str):
    ctx.ensure_object(dict)
    ctx.obj["store"] = Path(store).absolute()
    Repo.validate_path(ctx.obj["store"])


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
