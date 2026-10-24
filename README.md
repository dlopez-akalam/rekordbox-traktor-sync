# rb2traktor

A small command-line tool that copies **BPM**, **musical key** and **beatgrid**
from Rekordbox to Traktor. It reads a Rekordbox XML export and updates the
matching tracks in Traktor's `collection.nml`. Nothing else in the collection
is touched: cue points, loops, playlists, ratings and comments stay as they are.

Pure Python 3.8+, no dependencies.

## Install

```sh
pip install .            # or: pipx install .
rb2traktor --help
```

You can also run it without installing: `python3 -m rb2traktor ...`

## Usage

1. **Rekordbox**: *File → Export Collection in xml format* (e.g. `rekordbox.xml`).
2. **Quit Traktor.** Traktor overwrites `collection.nml` when it exits.
3. Preview the changes:

   ```sh
   rb2traktor rekordbox.xml --dry-run -v
   ```

   If you leave out the Traktor collection path, the newest
   `~/Documents/Native Instruments/Traktor*/collection.nml` is used. You can
   pass the path explicitly as the second argument.
4. Run it for real:

   ```sh
   rb2traktor rekordbox.xml
   ```

   A timestamped backup (`collection.nml.YYYYMMDD-HHMMSS.bak`) is written
   before the file is changed. Use `-o new.nml` to write a separate file instead.
5. Start Traktor.

## What gets written

| Rekordbox (XML)               | Traktor (NML)                                                           |
|-------------------------------|-------------------------------------------------------------------------|
| `AverageBpm`                  | `<TEMPO BPM=…>`                                                         |
| `Tonality` (`Am`, `8A`, `1m`) | `<MUSICAL_KEY VALUE=…>` and the key text `<INFO KEY=…>`                 |
| `<TEMPO Inizio Bpm Battito>`  | grid markers `<CUE_V2 TYPE="4">` with `<GRID BPM=…>`                    |

* **Beatgrid**: existing Traktor grid markers are replaced. Other cues are kept.
  The marker is placed on the first **downbeat**, using Rekordbox's
  `Battito` value, so bar counting in Traktor lines up with Rekordbox.
* **Variable tempo**: tracks analysed with Rekordbox's dynamic mode have several
  tempo segments. The default `--grid-mode single` writes one marker at the
  average BPM, and its grid will drift. `--grid-mode multi` writes one marker
  per tempo change.
* **Locking**: updated tracks are locked (`LOCK="1"`) so Traktor's analysis
  won't overwrite them. Turn this off with `--no-lock`. Tracks that were already
  locked are updated too, unless you pass `--skip-locked`.
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
rb2traktor REKORDBOX_XML [TRAKTOR_NML]
  -o, --output FILE        write to FILE instead of updating in place
  -n, --dry-run            show what would change, write nothing
  --no-backup              skip the timestamped backup
  --no-bpm / --no-key / --no-grid
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
python3 -m unittest -v
```
