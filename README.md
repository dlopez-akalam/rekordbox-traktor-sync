# rb2traktor

A small command-line tool that copies **BPM**, **musical key** and **beatgrid**
from Rekordbox to Traktor. It updates the matching tracks in Traktor's
`collection.nml`. Nothing else in the collection is touched: cue points, loops,
playlists, ratings and comments stay as they are.

It can read Rekordbox data in two ways:

* **Directly from the Rekordbox library** (`master.db` plus the analysis files).
  No export step. Needs the optional `pyrekordbox` package.
* **From a Rekordbox XML export.** Pure Python 3.8+, no dependencies.

## Install

```sh
pip install '.[db]'      # with direct library reading (recommended)
pip install .            # XML export only, no dependencies
rb2traktor --help
```

You can also run it without installing: `python3 -m rb2traktor ...`

## Usage

1. **Quit Traktor**: it overwrites `collection.nml` when it exits. Quitting
   Rekordbox too is best, so that all your edits are saved to its database.
2. Preview the changes:

   ```sh
   rb2traktor --dry-run -v
   ```

3. Run it for real:

   ```sh
   rb2traktor
   ```

   A timestamped backup (`collection.nml.YYYYMMDD-HHMMSS.bak`) is written
   before the file is changed. Use `-o new.nml` to write a separate file instead.
4. Start Traktor.

With no arguments, both libraries are found automatically:

* **Traktor**: the newest `~/Documents/Native Instruments/Traktor*/collection.nml`.
  To use a different file, pass it: `rb2traktor path/to/collection.nml`.
* **Rekordbox**: the source is picked in this order:
  1. **The Rekordbox library itself**, if `pyrekordbox` is installed and can open it.
  2. **The XML export at a fixed location**: `~/Music/rekordbox.xml`, or the path
     in the `RB2TRAKTOR_XML` environment variable. To use this route, export
     with *File → Export Collection in xml format* to that same file each time;
     Rekordbox remembers the folder. The tool warns you when the export is older
     than your Rekordbox library.

You can also choose the source explicitly:

```sh
rb2traktor rekordbox.xml                  # this XML export
rb2traktor /path/to/master.db             # this Rekordbox database
rb2traktor --from xml                     # always use the default XML export
rb2traktor --from db                      # always use the database; fail if it can't be read
rb2traktor rekordbox.xml collection.nml   # both explicit
```

### About reading the Rekordbox library directly

Rekordbox 6 and 7 keep their library in an encrypted SQLite database
(`master.db`), and each track's beatgrid in separate analysis files (`ANLZ*.DAT`).
[pyrekordbox](https://github.com/dylanljones/pyrekordbox) decrypts and parses
both. The database key is the same for every installation, and `pyrekordbox`
finds it automatically. If that ever fails, pass it with `--db-key`.

The tool only reads from the database: it never writes or commits anything to
Rekordbox's files.
These formats are undocumented, so a Rekordbox update could break this route
until `pyrekordbox` catches up. The XML export always remains a fallback.

## Existing Traktor values vs. `--overwrite`

By default the tool **only fills in what is missing**. If a Traktor track has
no BPM, no key or no beatgrid, that field is copied from Rekordbox. Values that
already exist in Traktor are left alone. They are reported as "kept existing"
(listed with `-v`).

To replace existing Traktor values with Rekordbox's, use `--overwrite`:

```sh
rb2traktor rekordbox.xml --overwrite all                         # BPM, key and grid
rb2traktor rekordbox.xml --overwrite grid                        # only replace grids
rb2traktor rekordbox.xml --overwrite bpm --overwrite grid        # BPM and grid, keep keys
```

Run it with `--dry-run -v` first to see exactly which tracks would change.

## What gets written

| Rekordbox XML                 | Rekordbox library        | Traktor (NML)                                           |
|-------------------------------|--------------------------|---------------------------------------------------------|
| `AverageBpm`                  | `djmdContent.BPM`        | `<TEMPO BPM=…>`                                         |
| `Tonality` (`Am`, `8A`, `1m`) | `djmdKey.ScaleName`      | `<MUSICAL_KEY VALUE=…>` and the key text `<INFO KEY=…>` |
| `<TEMPO Inizio Bpm Battito>`  | ANLZ beat grid (`PQTZ`)  | grid markers `<CUE_V2 TYPE="4">` with `<GRID BPM=…>`    |

* **Beatgrid**: when the grid is written, existing Traktor grid markers are
  replaced. Other cues are kept.
  The marker is placed on the first **downbeat**, using Rekordbox's
  `Battito` value, so bar counting in Traktor lines up with Rekordbox.
* **Variable tempo**: tracks analysed with Rekordbox's dynamic mode have several
  tempo segments (one per tempo change). The default `--grid-mode single` writes one marker at the
  average BPM, and its grid will drift. `--grid-mode multi` writes one marker
  per tempo change.
* **Locking**: updated tracks are locked (`LOCK="1"`) so Traktor's analysis
  won't overwrite them. Turn this off with `--no-lock`. Tracks that were already
  locked are treated like any other track, unless you pass `--skip-locked`.
* **Key text** is written in Open Key notation (Traktor's default). Use
  `--key-format musical` or `--key-format camelot` to change this.
* Running the tool again is safe. Tracks that already match are reported as
  unchanged and are not rewritten.

## Track matching

Tracks are matched by file path relative to the drive or volume, ignoring case.
So `C:\Music\a.mp3` matches Traktor's `C:` + `/:Music/:` + `a.mp3`, and
`/Volumes/USB/Music/a.mp3` matches volume `USB`. If there is no path match, the
tool falls back to the filename, but only when that filename is unique in the
Traktor collection. Turn the fallback off with `--no-filename-match`.

If your files live somewhere else in Traktor (for example, a different OS or
drive), rewrite the paths:

```sh
rb2traktor rekordbox.xml --path-map "C:/Users/me/Music=/Users/me/Music"
```

Use `-vv` to list the tracks that could not be matched. The tool only updates
tracks that already exist in Traktor. Import new tracks into Traktor first.

## Grid offset (MP3)

For some MP3 files, Rekordbox and Traktor decode the start of the file
differently (encoder delay / LAME header handling). Then the whole grid is
shifted by a constant amount, often around 26 ms. If you see this, check one
track and compensate:

```sh
rb2traktor rekordbox.xml --offset-ms -26
```

## Options

```
rb2traktor [REKORDBOX_SOURCE] [TRAKTOR_NML]
  --from auto|db|xml       where to read Rekordbox data (default: auto)
  --db-key KEY             Rekordbox database key, if detection fails
  -o, --output FILE        write to FILE instead of updating in place
  -n, --dry-run            show what would change, write nothing
  --no-backup              skip the timestamped backup
  --no-bpm / --no-key / --no-grid
  --overwrite all|bpm|key|grid  replace existing Traktor values (repeatable)
  --grid-mode single|multi
  --offset-ms MS
  --key-format openkey|musical|camelot
  --no-lock, --skip-locked
  --path-map OLD=NEW       (repeatable)
  --no-filename-match
  -v / -vv
```

## Development

```sh
pip install pyrekordbox   # optional: also runs the database tests
python3 -m unittest -v
```
