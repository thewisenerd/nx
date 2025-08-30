# /// script
# dependencies = [
#   "libtorrent~=2.0.11",
# ]
# ///

import libtorrent as lt
import hashlib
import os
from typing import Tuple


def straightforward(
    torrent_path: str,
    save_path: str,
):
    # Create session
    ses = lt.session()

    # Load torrent
    info = lt.torrent_info(torrent_path)
    params = {
        "ti": info,
        "save_path": save_path,
        "storage_mode": lt.storage_mode_t.storage_mode_sparse,
    }
    handle = ses.add_torrent(params)

    # Force a re-check of all files
    handle.force_recheck()

    # Wait until finished
    print("Rechecking...")
    while (
        handle.status().state != lt.torrent_status.seeding
        and handle.status().state != lt.torrent_status.downloading
    ):
        s = handle.status()
        print(f"progress: {s.progress * 100:.2f}% state: {s.state}")


def read_piece(ti: lt.torrent_info, piece_index: int, save_path: str) -> bytes:
    piece_size: int = ti.piece_size(piece_index)
    offset: int = piece_index * ti.piece_length()

    buf: bytearray = bytearray()

    fs: lt.file_storage = ti.files()
    for file_index in range(fs.num_files()):
        file_offset: int = fs.file_offset(file_index)
        file_size: int = fs.file_size(file_index)
        file_path: str = os.path.join(save_path, fs.file_path(file_index))

        if offset >= file_offset + file_size or offset + piece_size <= file_offset:
            continue

        start: int = max(0, offset - file_offset)
        end: int = min(file_size, offset + piece_size - file_offset)

        with open(file_path, "rb") as fh:
            fh.seek(start)
            buf.extend(fh.read(end - start))

    return bytes(buf[:piece_size])


def verify_torrent(torrent_path: str, save_path: str) -> Tuple[int, int]:
    ti: lt.torrent_info = lt.torrent_info(torrent_path)

    print(f"Checking torrent: {ti.name()}")
    print(f"Total pieces: {ti.num_pieces()} (piece length = {ti.piece_length()} bytes)")

    good: int = 0
    bad: int = 0

    fs: lt.file_storage = ti.files()
    total_sz: int = fs.total_size()

    idx = 0
    sz = 0
    for i in range(ti.num_pieces()):
        print(f"{idx}/{ti.num_pieces()}")
        idx += 1
        data: bytes = read_piece(ti, i, save_path)
        sz += len(data)
        h: bytes = hashlib.sha1(data).digest()
        expected: bytes = ti.hash_for_piece(i)

        if h == expected:
            good += 1
        else:
            bad += 1
            print(f"Piece {i} FAILED (size={len(data)})")

    print(
        f"Verification complete: {good} good, {bad} bad pieces {sz}, {total_sz == sz}"
    )
    return good, bad


if __name__ == "__main__":
    torrent_path = "E6E2AB75C79379259D1AE608839A28F94056390E.torrent"
    save_path = "./"
    # straightforward(torrent_path=torrent_path, save_path=save_path)
    verify_torrent(torrent_path=torrent_path, save_path=save_path)
