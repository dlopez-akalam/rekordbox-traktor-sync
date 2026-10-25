"""Command line interface: ``rb2traktor [REKORDBOX_SOURCE] [TRAKTOR_NML]``."""

import argparse
import os
import shutil
import sys
import time
from collections import Counter

from . import __version__
from .keys import KEY_FORMATS
from .rekordbox import read_rekordbox_xml
from .rekordbox_db import DatabaseUnavailable, default_db_paths, read_rekordbox_db
from .sync import OVERWRITE_FIELDS, SyncOptions, sync
from .traktor import TraktorCollection, find_collections

DEFAULT_XML = os.path.join("~", "Music", "rekordbox.xml")


def _path_map(value):
    if "=" not in value:
        raise argparse.ArgumentTypeError("expected OLD=NEW, got %r" % value)
    old, new = value.split("=", 1)
    return old, new


def _os_error(exc):
    """Format an OSError, explaining macOS privacy blocks (EPERM on ~/Documents etc.)."""
    msg = str(exc)
    if isinstance(exc, PermissionError) and sys.platform == "darwin":
        msg += ("\n  macOS blocked access to this folder. Allow your terminal app under "
                "System Settings > Privacy & Security > Files and Folders (Documents) "
                "or Full Disk Access, then run again.")
    return msg


class SourceError(Exception):
    pass


def _source_type(args):
    if args.source_type != "auto":
        return args.source_type
    if args.source:
        return "db" if args.source.lower().endswith(".db") else "xml"
    return "auto"


def _default_xml():
    return os.path.expanduser(os.environ.get("RB2TRAKTOR_XML") or DEFAULT_XML)


def _warn_if_stale(xml_path):
    """Warn when the Rekordbox library changed after the XML was exported."""
    try:
        exported = os.path.getmtime(xml_path)
    except OSError:
        return
    for db in default_db_paths():
        if db.stat().st_mtime > exported + 60:
            print("warning: %s is older than your Rekordbox library (%s); "
                  "re-export it if you changed BPM, keys or grids since"
                  % (xml_path, time.strftime("%Y-%m-%d %H:%M", time.localtime(exported))),
                  file=sys.stderr)
            return


def _read_db(path, key):
    tracks, warnings = read_rekordbox_db(path, key)
    for w in warnings:
        print("warning: %s" % w, file=sys.stderr)
    return tracks


def load_rekordbox(args):
    """Load Rekordbox tracks from the source chosen on the command line."""
    kind = _source_type(args)
    if kind == "db":
        try:
            tracks = _read_db(args.source, args.db_key)
        except DatabaseUnavailable as exc:
            raise SourceError(str(exc))
        print("Read %d tracks from the Rekordbox database" % len(tracks))
        return tracks

    if kind == "xml":
        xml = args.source or _default_xml()
        if not os.path.isfile(xml):
            raise SourceError("%s not found; export it from Rekordbox via "
                              "File > Export Collection in xml format" % xml)
        _warn_if_stale(xml)
        print("Using Rekordbox XML: %s" % xml)
        return read_rekordbox_xml(xml)

    # auto: prefer the live database, fall back to the usual XML export.
    try:
        tracks = _read_db(None, args.db_key)
        print("Read %d tracks from the Rekordbox database" % len(tracks))
        return tracks
    except DatabaseUnavailable as exc:
        reason = str(exc)
    xml = _default_xml()
    if not os.path.isfile(xml):
        raise SourceError(
            "no Rekordbox source available.\n"
            "  database: %s\n"
            "  XML: %s not found (export it via File > Export Collection in xml "
            "format, or pass its path)" % (reason, xml))
    print("Rekordbox database not used (%s)" % reason)
    _warn_if_stale(xml)
    print("Using Rekordbox XML: %s" % xml)
    return read_rekordbox_xml(xml)


def build_parser():
    p = argparse.ArgumentParser(
        prog="rb2traktor",
        description="Copy BPM, musical key and beatgrid from Rekordbox into a Traktor "
                    "collection.nml. Close Traktor before running.",
    )
    p.add_argument("source", nargs="?", metavar="REKORDBOX_SOURCE",
                   help="Rekordbox XML export or master.db. Default: read the Rekordbox "
                        "library directly (needs pyrekordbox), falling back to "
                        "$RB2TRAKTOR_XML or %s" % DEFAULT_XML)
    p.add_argument("traktor_nml", nargs="?",
                   help="Traktor collection.nml (default: newest one under "
                        "~/Documents/Native Instruments/Traktor*/)")
    p.add_argument("--from", dest="source_type", choices=("auto", "db", "xml"), default="auto",
                   help="where to read Rekordbox data from (default: auto)")
    p.add_argument("--db-key", help="Rekordbox database key, if automatic detection fails")
    p.add_argument("-o", "--output",
                   help="write the result here instead of updating TRAKTOR_NML in place")
    p.add_argument("-n", "--dry-run", action="store_true",
                   help="show what would change without writing anything")
    p.add_argument("--no-backup", action="store_true",
                   help="do not create a timestamped backup before overwriting the collection")

    what = p.add_argument_group("what to sync")
    what.add_argument("--no-bpm", dest="bpm", action="store_false", help="do not sync BPM")
    what.add_argument("--no-key", dest="key", action="store_false", help="do not sync musical key")
    what.add_argument("--no-grid", dest="grid", action="store_false", help="do not sync beatgrid")
    what.add_argument("--overwrite", action="append", default=[],
                      choices=("all",) + OVERWRITE_FIELDS, metavar="{all,bpm,key,grid}",
                      help="replace values that already exist in Traktor (repeatable, e.g. "
                           "--overwrite bpm --overwrite grid). By default only tracks that "
                           "have no BPM/key/grid in Traktor are filled in")

    grid = p.add_argument_group("beatgrid")
    grid.add_argument("--grid-mode", choices=("single", "multi"), default="single",
                      help="single: one grid marker on the first downbeat (default); "
                           "multi: one marker per tempo change for variable-tempo tracks")
    grid.add_argument("--offset-ms", type=float, default=0.0,
                      help="shift every grid marker by this many milliseconds "
                           "(compensates for MP3 decoder offset differences)")

    key = p.add_argument_group("key")
    key.add_argument("--key-format", choices=KEY_FORMATS, default="openkey",
                     help="notation written into Traktor's key text (default: openkey)")

    lock = p.add_argument_group("locking")
    lock.add_argument("--no-lock", dest="lock", action="store_false",
                      help="do not lock updated tracks (Traktor may re-analyse and overwrite them)")
    lock.add_argument("--skip-locked", action="store_true",
                      help="leave tracks that are already locked in Traktor untouched")

    match = p.add_argument_group("track matching")
    match.add_argument("--path-map", type=_path_map, action="append", default=[],
                       metavar="OLD=NEW",
                       help="rewrite a Rekordbox path prefix before matching, e.g. "
                            "'C:/Music=/Volumes/USB/Music' (repeatable)")
    match.add_argument("--no-filename-match", dest="match_filename", action="store_false",
                       help="only match by full path, never by unique filename")

    p.add_argument("-v", "--verbose", action="count", default=0,
                   help="-v lists changed tracks, -vv also lists unchanged/unmatched")
    p.add_argument("--version", action="version", version="%(prog)s " + __version__)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)

    # "rb2traktor collection.nml" means: default Rekordbox source, this collection.
    if args.source and not args.traktor_nml and args.source.lower().endswith(".nml"):
        args.source, args.traktor_nml = None, args.source

    nml = args.traktor_nml
    if not nml:
        try:
            candidates = find_collections()
        except OSError as exc:
            print("error: %s" % _os_error(exc), file=sys.stderr)
            return 2
        if not candidates:
            print("error: no Traktor collection.nml found; pass its path explicitly",
                  file=sys.stderr)
            return 2
        nml = candidates[0]
        print("Using Traktor collection: %s" % nml)
        if len(candidates) > 1:
            # e.g. Traktor Pro 3 and 4 side by side, or an older 4.x folder left
            # behind by an update: the most recently saved one is assumed live.
            print("note: also found %s; this is the most recently saved one. Pass the "
                  "path explicitly to use another." % ", ".join(candidates[1:]))

    try:
        rb_tracks = load_rekordbox(args)
    except OSError as exc:
        print("error: %s" % _os_error(exc), file=sys.stderr)
        return 2
    except (SourceError, ValueError) as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2
    except Exception as exc:  # xml.etree.ElementTree.ParseError and friends
        print("error: could not parse Rekordbox data: %s" % exc, file=sys.stderr)
        return 2

    try:
        collection = TraktorCollection(nml)
    except OSError as exc:
        print("error: %s" % _os_error(exc), file=sys.stderr)
        return 2
    except ValueError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2
    except Exception as exc:  # xml.etree.ElementTree.ParseError and friends
        print("error: could not parse Traktor collection: %s" % exc, file=sys.stderr)
        return 2

    options = SyncOptions(
        bpm=args.bpm, key=args.key, grid=args.grid,
        grid_mode=args.grid_mode, offset_ms=args.offset_ms,
        key_format=args.key_format,
        overwrite=frozenset(OVERWRITE_FIELDS if "all" in args.overwrite else args.overwrite),
        lock=args.lock, skip_locked=args.skip_locked,
        match_filename=args.match_filename, path_maps=args.path_map,
    )
    results = sync(rb_tracks, collection, options)
    counts = Counter(r.status for r in results)

    for r in results:
        show = (r.status in ("updated", "kept") and args.verbose >= 1) or args.verbose >= 2
        if show:
            detail = "; ".join(r.changes + ["kept existing %s" % f for f in r.kept])
            print("[%s] %s%s" % (r.status, r.rb.label, (": " + detail) if detail else ""))
        for w in r.warnings:
            print("warning: %s: %s" % (r.rb.label, w), file=sys.stderr)

    print("Rekordbox tracks: %d, Traktor entries: %d" % (len(rb_tracks), len(collection.entries)))
    print("  updated:   %d" % counts["updated"])
    print("  unchanged: %d" % counts["unchanged"])
    if counts["kept"]:
        print("  kept existing Traktor values: %d (use --overwrite to replace them)"
              % counts["kept"])
    if counts["locked"]:
        print("  skipped (locked in Traktor): %d" % counts["locked"])
    print("  not in Traktor: %d" % counts["unmatched"])
    if counts["ambiguous"]:
        print("  ambiguous match (skipped): %d" % counts["ambiguous"])
    if counts["unmatched"] and args.verbose < 2:
        print("  (run with -vv to list unmatched tracks, or use --path-map if your "
              "music lives at a different location in Traktor)")

    if args.dry_run:
        print("Dry run: nothing written.")
        return 0
    if not counts["updated"] and not args.output:
        print("Nothing to write.")
        return 0

    out = args.output or nml
    try:
        if os.path.abspath(out) == os.path.abspath(nml) and not args.no_backup:
            backup = "%s.%s.bak" % (nml, time.strftime("%Y%m%d-%H%M%S"))
            shutil.copy2(nml, backup)
            print("Backup written to %s" % backup)
        collection.write(out)
    except OSError as exc:
        print("error: %s" % _os_error(exc), file=sys.stderr)
        return 2
    print("Wrote %s" % out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
