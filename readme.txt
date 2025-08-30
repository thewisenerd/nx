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

releases are usually single-file, or single-folder.

let's take the following examples.

```
- Inception.2010.mkv
+ Inception.2010/
+   Inception.2010.mkv
+   .nx_store {}

 Interstellar.2014/
   release.nfo
   Interstellar.2014.mkv
+  .nx_store {entry.strip-components=1}
```


### nx cli

`nx`

```
no entries found
```

```
{0x00 infohash}
  - torrent: title 0x00
    |- announce
    |  - tracker1
    |  - tracker2
    |- info
    |  - files (NN GB)
    |    - folder1 (NN GB)
    |      - file1 (NN GB)
    |- private?
  - nx
    |- @internal
    |  - strip-components
    |  - last-verified:
    |    - ts: {epoch_second}
    |    - status: ""
    |- custom
    |  - ??
- {0x01 infohash}
  - torrent: title 0x01
    |- announce ...
  - nx
    |- @internal: ...
    |- custom: ...
```

---

`nx add {torrent}`

`nx add {magnet}`

---

`nx verify {id}`

`nx verify -a|--all`

---

`nx sync` (WIP)

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
