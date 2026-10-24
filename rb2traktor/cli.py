"""Command line interface: ``rb2traktor REKORDBOX_XML [TRAKTOR_NML]``."""

import argparse
import os
import shutil
import sys
import time
from collections import Counter

from . import __version__
from .keys import KEY_FORMATS
from .rekordbox import read_rekordbox_xml
from .sync import SyncOptions, sync
from .traktor import TraktorCollection, find_collections


def _path_map(value):
    if "=" not in value:
        raise argparse.ArgumentTypeError("expected OLD=NEW, got %r" % value)
    old, new = value.split("=", 1)
    return old, new


def build_parser():
    p = argparse.ArgumentParser(
        prog="rb2traktor",
        description="Copy BPM, musical key and beatgrid from a Rekordbox XML export "
                    "into a Traktor collection.nml. Close Traktor before running.",
    )
    p.add_argument("rekordbox_xml",
                   help="Rekordbox collection exported via File > Export Collection in xml format")
    p.add_argument("traktor_nml", nargs="?",
                   help="Traktor collection.nml (default: newest one under "
                        "~/Documents/Native Instruments/Traktor*/)")
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

    nml = args.traktor_nml
    if not nml:
        candidates = find_collections()
        if not candidates:
            print("error: no Traktor collection.nml found; pass its path explicitly",
                  file=sys.stderr)
            return 2
        nml = candidates[0]
        print("Using Traktor collection: %s" % nml)

    try:
        rb_tracks = read_rekordbox_xml(args.rekordbox_xml)
        collection = TraktorCollection(nml)
    except (OSError, ValueError) as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2
    except Exception as exc:  # xml.etree.ElementTree.ParseError and friends
        print("error: could not parse input: %s" % exc, file=sys.stderr)
        return 2

    options = SyncOptions(
        bpm=args.bpm, key=args.key, grid=args.grid,
        grid_mode=args.grid_mode, offset_ms=args.offset_ms,
        key_format=args.key_format, lock=args.lock, skip_locked=args.skip_locked,
        match_filename=args.match_filename, path_maps=args.path_map,
    )
    results = sync(rb_tracks, collection, options)
    counts = Counter(r.status for r in results)

    for r in results:
        show = (r.status == "updated" and args.verbose >= 1) or args.verbose >= 2
        if show:
            detail = "; ".join(r.changes) if r.changes else ""
            print("[%s] %s%s" % (r.status, r.rb.label, (": " + detail) if detail else ""))
        for w in r.warnings:
            print("warning: %s: %s" % (r.rb.label, w), file=sys.stderr)

    print("Rekordbox tracks: %d, Traktor entries: %d" % (len(rb_tracks), len(collection.entries)))
    print("  updated:   %d" % counts["updated"])
    print("  unchanged: %d" % counts["unchanged"])
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
    if os.path.abspath(out) == os.path.abspath(nml) and not args.no_backup:
        backup = "%s.%s.bak" % (nml, time.strftime("%Y%m%d-%H%M%S"))
        shutil.copy2(nml, backup)
        print("Backup written to %s" % backup)
    collection.write(out)
    print("Wrote %s" % out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
