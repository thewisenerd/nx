import time
import urllib.parse
from collections import Counter
from collections.abc import Callable, Iterator
from pathlib import Path

import click
import structlog
from rich.console import Console

from .cli_helpers import (
    _calculate_unique_prefixes,
    _print_torrent_entry,
    _print_torrent_info,
)
from .click_pathtype import PathType
from .config import cache_dir, config_dir, parse_config
from .import_torrents import (
    ImportResult,
    ImportStatus,
    discover_torrent_files,
    find_torrent_roots,
)
from .nx import Torrent, parse_torrent, parse_torrent_buf
from .redact import RedactionRule, build_redaction_rules, redact_torrent_buffer
from .store import DefaultStorePathName, Repo, TorrentEntry, load
from .torrent_cache import TorrentCacheError, download_from_cache, fetch_torrent_url

logger: structlog.stdlib.BoundLogger = structlog.get_logger(__name__)


_default_store_path = Path(DefaultStorePathName).absolute()

console = Console()

ctx_keys = {
    "root_path": "root_path",
    "store_path": "store_path",
    "max_announce_count": "max_announce_count",
    "max_files": "max_files",
    "root_explicit": "root_explicit",
}


def _get_root_path(ctx: click.Context) -> Path:
    root: Path = ctx.obj[ctx_keys["root_path"]]
    return root


def _get_store_path(ctx: click.Context) -> Path | None:
    store: Path | None = ctx.obj.get(ctx_keys["store_path"])
    return store


@click.group(
    invoke_without_command=True,
    epilog=(
        "Configuration is read from $XDG_CONFIG_HOME/nx/config.yaml "
        "(default: ~/.config/nx/config.yaml). Run 'nx config --help' for "
        "available settings."
    ),
)
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
    ctx.obj[ctx_keys["root_explicit"]] = root is not None
    ctx.obj[ctx_keys["store_path"]] = root_path / DefaultStorePathName if root else None
    ctx.obj[ctx_keys["max_announce_count"]] = max_announce_count
    ctx.obj[ctx_keys["max_files"]] = max_files

    store_path = _get_store_path(ctx)
    if store_path:
        Repo.validate_path(store_path)

    if ctx.invoked_subcommand is None:
        _show_entries(_get_store_path(ctx), max_announce_count, max_files)


@nx.command(
    help="show the global configuration path",
    epilog="""\
\b
Available settings:
  proxy       Proxy URL used for HTTP operations.
  redactions  Tracker announce URL redaction rules.
  secrets     Secret values associated with redaction rules (reserved).

\b
Example:
  proxy: "socks5://10.64.0.1:1080"

\b
  redactions:
    example:
      pattern: '^https://tracker\\.example/(?P<key>[^/]+)/announce$'
      template: 'https://tracker.example/{key}/announce'
""",
)
def config() -> None:
    """Describe global configuration."""
    click.echo(config_dir / "config.yaml")


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


def _download_torrent_url(url: str, /, label: str) -> bytes:
    try:
        return fetch_torrent_url(url)
    except TorrentCacheError as error:
        click.echo(f"failed to download torrent for {label}: {error}", err=True)
        raise click.Abort() from error


def _download_magnet(infohash: str) -> bytes:
    try:
        return download_from_cache(infohash, validate=parse_torrent_buf)
    except TorrentCacheError as error:
        click.echo(
            f"failed to download torrent for magnet link: {infohash}: {error}", err=True
        )
        raise click.Abort() from error


def _parse_sha1_infohash(value: str, label: str) -> str:
    infohash = value.upper()
    if len(infohash) != 40:
        click.echo(f"{label} is not a valid SHA1 infohash", err=True)
        raise click.Abort()

    try:
        bytes.fromhex(infohash)
    except ValueError:
        click.echo(f"{label} is not a valid SHA1 infohash", err=True)
        raise click.Abort()

    return infohash


def _parse_magnet(parsed: urllib.parse.ParseResult) -> str:
    if parsed.netloc:
        if parsed.path or parsed.params or parsed.query or parsed.fragment:
            click.echo("invalid magnet shorthand form", err=True)
            raise click.Abort()
        return _parse_sha1_infohash(parsed.netloc, "magnet link infohash")

    if parsed.path or parsed.params or parsed.fragment:
        click.echo("invalid magnet link", err=True)
        raise click.Abort()

    params = urllib.parse.parse_qs(parsed.query)
    xt = params.get("xt", [])
    if not xt:
        click.echo("magnet link missing 'xt' parameter", err=True)
        raise click.Abort()
    if len(xt) != 1:
        click.echo("magnet link must contain exactly one 'xt' parameter", err=True)
        raise click.Abort()

    parts = xt[0].split(":")
    if len(parts) != 3 or parts[0] != "urn" or parts[1] != "btih":
        click.echo("magnet link 'xt' parameter is not a valid btih urn", err=True)
        raise click.Abort()

    return _parse_sha1_infohash(parts[2], "magnet link 'xt' infohash")


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


def _import_one(
    source: Path,
    library_root: Path,
    redaction_rules: list[RedactionRule],
    *,
    dry_run: bool,
) -> ImportResult:
    try:
        buffer = redact_torrent_buffer(source.read_bytes(), redaction_rules)
        torrent = parse_torrent_buf(buffer)
    except (OSError, RuntimeError, ValueError) as error:
        return ImportResult(source, ImportStatus.INVALID, detail=str(error))

    root_ref = torrent.strip_root()
    if root_ref is None:
        return ImportResult(source, ImportStatus.SINGLE_FILE, infohash=torrent.infohash)

    candidates = find_torrent_roots(torrent, library_root)
    if not candidates:
        return ImportResult(source, ImportStatus.NOT_FOUND, infohash=torrent.infohash)
    if len(candidates) > 1:
        detail = ", ".join(str(path.relative_to(library_root)) for path in candidates)
        return ImportResult(
            source,
            ImportStatus.AMBIGUOUS,
            infohash=torrent.infohash,
            detail=detail,
        )

    target = candidates[0]
    try:
        with Repo(target / DefaultStorePathName) as repo:
            existing = repo.store.get_torrent(torrent.infohash)
            if existing is not None and existing.nx["@internal"].strip_components != 1:
                return ImportResult(
                    source,
                    ImportStatus.ERROR,
                    target=target,
                    infohash=torrent.infohash,
                    detail="existing entry has incompatible strip-components",
                )
            if existing is not None and existing.nx["@internal"].ready:
                return ImportResult(
                    source,
                    ImportStatus.EXISTING_READY,
                    target=target,
                    infohash=torrent.infohash,
                )

            if dry_run:
                status = (
                    ImportStatus.WOULD_VERIFY
                    if existing is not None
                    else ImportStatus.WOULD_IMPORT
                )
                return ImportResult(
                    source, status, target=target, infohash=torrent.infohash
                )

            if not torrent.verify_pieces(target, strip_components=1):
                return ImportResult(
                    source,
                    ImportStatus.VERIFICATION_FAILED,
                    target=target,
                    infohash=torrent.infohash,
                )

            entry = existing or TorrentEntry.from_torrent(torrent, strip_components=1)
            entry.nx["@internal"].ready = True
            entry.nx["@internal"].last_verified = int(time.time())
            repo.store.upsert(entry)
            status = (
                ImportStatus.EXISTING_VERIFIED
                if existing is not None
                else ImportStatus.IMPORTED
            )
            return ImportResult(
                source, status, target=target, infohash=torrent.infohash
            )
    except (OSError, ValueError) as error:
        return ImportResult(
            source,
            ImportStatus.ERROR,
            target=target,
            infohash=torrent.infohash,
            detail=str(error),
        )


def _print_import_results(results: list[ImportResult], library_root: Path) -> None:
    for result in results:
        target = (
            str(result.target.relative_to(library_root))
            if result.target is not None
            else result.source.name
        )
        suffix = f" ({result.detail})" if result.detail else ""
        click.echo(f"{result.status.value:<20} {target}{suffix}")

    counts = Counter(result.status.value for result in results)
    summary = ", ".join(f"{count} {status}" for status, count in sorted(counts.items()))
    click.echo(f"\n{summary or 'no torrents found'}")


@nx.command(name="import", help="import matching multi-file torrents beneath the root")
@click.argument(
    "source",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
)
@click.option(
    "--dry-run", is_flag=True, help="show matches without verifying or writing stores"
)
@click.option(
    "--strict",
    is_flag=True,
    help="fail for single-file, unmatched, or ambiguous torrents",
)
@click.pass_context
def import_torrents(
    ctx: click.Context, source: Path, dry_run: bool, strict: bool
) -> None:
    if not ctx.obj[ctx_keys["root_explicit"]]:
        raise click.UsageError("import requires an explicit root; use -C/--root")

    library_root = _get_root_path(ctx)
    redaction_rules = build_redaction_rules(parse_config().redactions)
    results = [
        _import_one(path, library_root, redaction_rules, dry_run=dry_run)
        for path in discover_torrent_files(source)
    ]
    _print_import_results(results, library_root)

    failures = {
        ImportStatus.INVALID,
        ImportStatus.ERROR,
        ImportStatus.VERIFICATION_FAILED,
    }
    if strict:
        failures |= {
            ImportStatus.SINGLE_FILE,
            ImportStatus.NOT_FOUND,
            ImportStatus.AMBIGUOUS,
        }
    if any(result.status in failures for result in results):
        ctx.exit(1)


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
    config = parse_config()
    redacted_buffer = redact_torrent_buffer(
        torrent.buffer, build_redaction_rules(config.redactions)
    )
    if redacted_buffer != torrent.buffer:
        torrent = parse_torrent_buf(redacted_buffer)

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
