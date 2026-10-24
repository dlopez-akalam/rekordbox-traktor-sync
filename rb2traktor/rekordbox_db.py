"""Read tracks straight from Rekordbox's library (master.db + ANLZ analysis files).

This avoids the manual "Export Collection in xml format" step. It needs the
optional ``pyrekordbox`` package (``pip install rb2traktor[db]``), which knows how
to decrypt Rekordbox 6/7's SQLCipher database and parse the ANLZ files that
hold the beatgrids.
"""

import logging
import os
from pathlib import Path

from .grid import BPM_EPSILON
from .keys import parse_key
from .paths import location_to_path
from .rekordbox import RekordboxTrack, TempoSegment


class DatabaseUnavailable(Exception):
    """Raised when the Rekordbox database can't be used (missing package or file)."""


def default_db_paths():
    """Usual master.db locations, for staleness checks without pyrekordbox."""
    home = Path.home()
    candidates = [
        home / "Library" / "Pioneer" / "rekordbox" / "master.db",
        home / "Library" / "Application Support" / "Pioneer" / "rekordbox" / "master.db",
    ]
    appdata = os.environ.get("APPDATA")
    if appdata:
        candidates.insert(0, Path(appdata) / "Pioneer" / "rekordbox" / "master.db")
    return [p for p in candidates if p.is_file()]


def segments_from_beats(beats, bpms, times):
    """Collapse a per-beat ANLZ grid into tempo segments.

    ``beats`` are bar positions (1..4), ``bpms`` tempos and ``times`` beat
    positions in seconds, one entry per beat. A new segment starts wherever
    the tempo changes, mirroring the ``<TEMPO>`` entries of an XML export.
    """
    segments = []
    for beat, bpm, start in zip(beats, bpms, times):
        bpm = float(bpm)
        if not bpm:
            continue
        if segments and abs(segments[-1].bpm - bpm) < BPM_EPSILON:
            continue
        segments.append(TempoSegment(float(start), bpm, int(beat) or 1))
    return segments


def rekordbox_running():
    try:
        from pyrekordbox.utils import get_rekordbox_pid
        return bool(get_rekordbox_pid())
    except Exception:
        return False


def read_beat_grid(anlz_path):
    """Return ``(beats, bpms, times)`` from an ANLZ .DAT file, or None."""
    from pyrekordbox.anlz import AnlzFile

    anlz = AnlzFile.parse_file(anlz_path)
    if "PQTZ" not in anlz:
        return None
    return anlz.get("PQTZ")


def _first_line(exc):
    """SQLAlchemy errors embed the whole SQL statement; keep the useful part."""
    return (str(exc).splitlines() or [type(exc).__name__])[0]


def _open_database(path=None, key=None, unlock=True):
    try:
        try:
            from pyrekordbox import MasterDatabase
        except ImportError:  # pyrekordbox < 0.5
            from pyrekordbox import Rekordbox6Database as MasterDatabase
    except ImportError as exc:
        raise DatabaseUnavailable(
            "reading the Rekordbox library directly needs pyrekordbox: "
            "pip install 'rb2traktor[db]'") from exc
    # pyrekordbox logs routine notices (missing playlist XML, ...) as warnings
    # and resets its level on import, so quieten it only now.
    logging.getLogger("pyrekordbox").setLevel(logging.ERROR)
    try:
        return MasterDatabase(path=path, key=key or "", unlock=unlock)
    except Exception as exc:  # FileNotFoundError, ImportError (no sqlcipher), bad key...
        raise DatabaseUnavailable("could not open the Rekordbox database: %s"
                                  % _first_line(exc)) from exc


def read_rekordbox_db(path=None, key=None, unlock=True, read_grid=read_beat_grid):
    """Read all tracks from master.db (auto-detected when ``path`` is None).

    Returns ``(tracks, warnings)``.
    """
    db = _open_database(path, key, unlock)
    tracks, warnings = [], []
    if rekordbox_running():
        warnings.append("Rekordbox is running; recent edits may not be saved to its "
                        "database yet. Close Rekordbox for a reliable sync.")
    try:
        share = Path(db.share_directory)
        try:
            # SQLCipher only notices a wrong key on the first query.
            contents = list(db.get_content())
        except Exception as exc:
            msg = _first_line(exc)
            if "not a database" in msg:
                msg += " (wrong database key? try --db-key)"
            raise DatabaseUnavailable("could not read the Rekordbox database: %s" % msg) from exc
        for content in contents:
            if getattr(content, "rb_local_deleted", 0) or not content.FolderPath:
                continue
            key_text = content.Key.ScaleName if content.Key is not None else ""
            artist = content.Artist.Name if content.Artist is not None else ""
            track = RekordboxTrack(
                track_id=str(content.ID),
                name=content.Title or "",
                artist=artist or "",
                path=location_to_path(content.FolderPath),
                bpm=content.BPM / 100.0 if content.BPM else None,
                key_text=key_text or "",
                key=parse_key(key_text),
            )
            if content.AnalysisDataPath:
                anlz_path = share / content.AnalysisDataPath.replace("\\", "/").strip("/")
                if anlz_path.is_file():
                    try:
                        grid = read_grid(anlz_path)
                    except Exception as exc:
                        warnings.append("%s: could not read beatgrid (%s)" % (track.label, exc))
                        grid = None
                    if grid is not None:
                        track.tempos = segments_from_beats(*grid)
            tracks.append(track)
    finally:
        db.close()
    return tracks, warnings
