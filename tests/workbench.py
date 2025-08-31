from pathlib import Path

from nx.nx import parse_torrent
from nx.store import Repo, TorrentEntry

if __name__ == "__main__":
    # verify_pieces(
    #     Path("../samples/E6E2AB75C79379259D1AE608839A28F94056390E.torrent"),
    #     Path("~/p2p"),
    # )

    # torr = parse_torrent(
    #     Path("../samples/2DA72B551DD309A22636F1B1668AE31F9200451B.torrent")
    # )
    # print(torr)

    torr = parse_torrent(
        Path("../samples/works_poe_raven_edition_vol2_0912_archive.torrent"),
    )

    with Repo(Path(".nx_store")) as repo:
        entry = TorrentEntry.from_torrent(torr)
        repo.store.upsert(entry)
