# nx

## concepts

### metadata store (`.nx_store`)

a metadata store contains references to one or more torrents.

a torrent has checksum data that can be used for file verification.
a torrent has announce data for registering the torrent in a client.

```
magic: {starts with "AF42", sha1 of "NXFS24757"}
checksum: {sha1 of entries}
entries:
- id: {0x00infohash}
  type: torrent
  torrent: {bytes}
  nx:
    @internal:
      ...
- id: {0x01infohash}
  type: torrent
  torrent: {bytes}
  nx: ...
```

#### where will the store be?

[BEP 0003][spec] states:

> In the single file case, the name key is the name of a file, in the muliple
> file case, it's the name of a directory.

[spec]: https://www.bittorrent.org/beps/bep_0003.html

in the case of single-file torrents, we want the store to be _beside_ the file.

single-file torrents require `-f`/`--here` to explicitly confirm the store
location.

```
  big_buck_bunny_1080p_h264.mov
+ .nx_store{entries=[...,strip-components=0]}
```

in the case of multi-file torrents, we want the store to be inside the root
directory

```
  Pioneer One Season One Complete XviD/
+   .nx_store{entries=[...,strip-components=1]}
    vodo.nfo
    Pioneer.One.S01E01.720x480_VODO_XviD.avi
    Pioneer.One.S01E02.720x480_VODO_XviD.avi
    ...
```

## installation

`git clone`, `uv sync`, and add the `bin/` dir to your path. _/shrug_.

## nx

```
Usage: nx [OPTIONS] COMMAND [ARGS]...

Options:
  -C, --root PATH                 operate in this directory
  --max-announce-count INTEGER RANGE
                                  maximum number of announce urls to show per
                                  torrent (0 = show all)  [x>=0]
  --max-files INTEGER RANGE       maximum number of files to show per torrent
                                  (0 = show all)  [x>=0]
  --help                          Show this message and exit.

Commands:
  add     add a torrent file to the store
  parse   parse a torrent file and display its info
  verify  verify the files for a torrent by its identifier (prefix)
```

### list

simply invoking `nx` lists the entries in the store.

```
20FD2B2A977871406E211606CAF7B65F412FE9FF
├── torrent: works_poe_raven_edition_vol2_0912
│   ├── announce
│   │   ├── http://bt1.archive.org:6969/announce
│   │   └── http://bt2.archive.org:6969/announce
│   ├── works_poe_raven_edition_vol2_0912/ (342.69 MB)
│   │   ├── CollectedWorksOfEdgarAllanPoeRavenEditionVolume2_librivox.m4b 
│   │   │   (299.10 MB)
│   │   ├── __ia_thumb.jpg (12.87 KB)
│   │   └── ravencollectedpoevol2_01_poe.mp3 (43.58 MB)
│   ├── ... and 172 more files
│   └── private: false
└── nx
    └── @internal
        ├── strip-components: 1
        └── ready: false
```

### add

```
Usage: nx add [OPTIONS] SOURCE

  add a torrent file to the store

Options:
  -f, --here  use current directory as root
  --help      Show this message and exit.
```

`nx add path/to/file.torrent`

`nx add "magnet:?xt=urn:btih:..."` / `nx add "magnet://{hash}"` (short-hand)

`nx add "https://example.com/path.torrent"`

### verify

```
Usage: nx verify [IDENTIFIER]

  verify torrents (all if no identifier given)

Options:
  --help  Show this message and exit.
```

`nx verify` — verify all

`nx verify {id}` — verify by prefix

## TODO

### add

- [ ] magnet support
    - [ ] simply fetch from iTorrents.org cache to start

### config

`$XDG_CONFIG_HOME/nx` (defaults to `~/.config/nx`) is the base directory.

`nx.conf` is a YAML file that may be used to configure certain options.

```
# nx.conf

# use proxy for http operations
# proxy: "socks5://10.64.0.1:1080"
```

### verify

- macOS files with unicode characters are NFD while linux/torrents are typically
  NFC?
    - a file rsync'ed from a macOS machine may have the wrong encoding
    - we may introduce a `--fix` option to allow verify to automatically fix
      this but it would be a misnomer

### sync

**goals**

- transmission
    - register a torrent
    - sync seeding ratios to store
- announce filtering
- validate DHT disabled before syncing private torrents

```
remotes:
- id: transmission
  host: localhost
  port: 9090
- id: rtorrent
  host: localhost
  port: 9091
```
