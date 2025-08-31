# nx

## concepts

### metadata store (`.nx_store`)

```
magic: 'nx001'
entries:
- id: {0x00infohash}
  @type: torrent
  torrent: {bytes}
  nx:
    @internal:
      ...
    custom:
      ...
- id: {0x01infohash}
  @type: torrent
  torrent: {bytes}
  nx: ...
checksum: {crc32 encoded with checksum field set to ""}
```

#### where will the store be?

[BEP 0003][spec] states:

> In the single file case, the name key is the name of a file, in the muliple
> file case, it's the name of a directory.

[spec]: https://www.bittorrent.org/beps/bep_0003.html

in the case of single-file torrents, we want the store to be _beside_ the file.

to prevent accidental store creations, you may not `nx add` a single-file
torrent without also specifying `-f`.

```
  Inception.2010.mkv
+ .nx_store{entries=[...,strip-components=0]}
```

in the case of multi-file torrents, we want the store to be inside the root
directory

```
  Interstellar.2014/
+   .nx_store{entries=[...,strip-components=1]}
    release.nfo
    Interstellar.2014.mkv
```

### nx cli

```
Usage: nx [OPTIONS] COMMAND [ARGS]...

Options:
  -s, --store TEXT
  --max-announce-count INTEGER  maximum number of announce urls to show per
                                torrent (0 = show all)
  --max-files INTEGER           maximum number of files to show per torrent (0
                                = show all)
  --help                        Show this message and exit.

Commands:
  add
  verify
```

#### examples

`nx`

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

#### add

```
Usage: nx add [OPTIONS] SOURCE

Options:
  --strip-components INTEGER
  --auto-strip-root / --no-auto-strip-root
  --help                          Show this message and exit.
```

`nx add path/to/file.torrent`

`nx add "magnet:?xt=urn:btih:..."`

#### verify

```
Usage: nx verify [OPTIONS] [IDENTIFIER]

Options:
  -a, --all  verify all torrents
  --help     Show this message and exit.
```

`nx verify {id}`

`nx verify -a|--all`

## TODO

### sync (WIP)

```
# ~/.config/nx/nx.conf

remotes:
- id: transmission
  host: localhost
  port: 9090
- id: rtorrent
  host: localhost
  port: 9091
```
