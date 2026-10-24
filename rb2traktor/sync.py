"""Match Rekordbox tracks to Traktor entries and copy BPM, key and beatgrid."""

from collections import defaultdict
from dataclasses import dataclass, field

from .grid import build_grid, is_constant
from .keys import format_key
from .paths import (apply_path_maps, basename_key, match_key, normalize_volume,
                    split_path)


@dataclass
class SyncOptions:
    bpm: bool = True
    key: bool = True
    grid: bool = True
    grid_mode: str = "single"
    offset_ms: float = 0.0
    key_format: str = "openkey"
    lock: bool = True
    skip_locked: bool = False
    match_filename: bool = True
    path_maps: list = field(default_factory=list)


@dataclass
class TrackResult:
    rb: object
    entry: object = None
    status: str = ""  # updated, unchanged, unmatched, ambiguous, locked
    changes: list = field(default_factory=list)
    warnings: list = field(default_factory=list)


class Matcher:
    def __init__(self, entries):
        self.by_path = defaultdict(list)
        self.by_name = defaultdict(list)
        for entry in entries:
            if not entry.rel_path:
                continue
            self.by_path[match_key(entry.rel_path)].append(entry)
            self.by_name[basename_key(entry.rel_path)].append(entry)

    def match_path(self, path):
        """Return ``(entry, status)`` where status is matched/unmatched/ambiguous."""
        volume, rel = split_path(path)
        candidates = self.by_path.get(match_key(rel), [])
        if len(candidates) > 1:
            vol = normalize_volume(volume)
            same = [e for e in candidates if normalize_volume(e.volume) == vol]
            candidates = same if same else candidates
        if len(candidates) == 1:
            return candidates[0], "matched"
        if len(candidates) > 1:
            return None, "ambiguous"
        return None, "unmatched"

    def match_filename(self, path):
        _, rel = split_path(path)
        candidates = self.by_name.get(basename_key(rel), [])
        if len(candidates) == 1:
            return candidates[0], "matched"
        if len(candidates) > 1:
            return None, "ambiguous"
        return None, "unmatched"


def match_tracks(rb_tracks, entries, options):
    """Pair every Rekordbox track with a Traktor entry (or a failure status).

    Exact path matches are resolved first so that the filename fallback can
    never take an entry that another track matches by full path.
    """
    matcher = Matcher(entries)
    pairs = []
    claimed = set()
    for rb in rb_tracks:
        entry, status = matcher.match_path(apply_path_maps(rb.path, options.path_maps))
        if entry is not None:
            claimed.add(id(entry))
        pairs.append([rb, entry, status])
    if options.match_filename:
        for pair in pairs:
            if pair[2] != "unmatched":
                continue
            entry, status = matcher.match_filename(apply_path_maps(pair[0].path, options.path_maps))
            if entry is not None and id(entry) in claimed:
                entry, status = None, "ambiguous"
            if entry is not None:
                claimed.add(id(entry))
            pair[1], pair[2] = entry, status
    return pairs


def _fmt_grid(markers):
    return ", ".join("%.1fms@%.2f" % (m[0], m[1]) if m[1] else "%.1fms" % m[0]
                     for m in markers) or "none"


def sync(rb_tracks, collection, options):
    results = []
    for rb, entry, status in match_tracks(rb_tracks, collection.entries, options):
        result = TrackResult(rb)
        results.append(result)
        if entry is None:
            result.status = status
            continue
        result.entry = entry

        if entry.locked and options.skip_locked:
            result.status = "locked"
            continue

        if options.bpm and rb.bpm:
            old = entry.bpm
            if old is None or abs(old - rb.bpm) >= 0.0005:
                entry.set_bpm(rb.bpm)
                result.changes.append("bpm %s -> %.2f" % ("%.2f" % old if old else "none", rb.bpm))

        if options.key:
            if rb.key is None:
                if rb.key_text:
                    result.warnings.append("unrecognised key %r" % rb.key_text)
            else:
                old = entry.key
                info = entry.element.find("INFO")
                old_text = info.get("KEY") if info is not None else None
                new_text = format_key(rb.key, options.key_format)
                if old != rb.key or old_text != new_text:
                    entry.set_key(rb.key, options.key_format)
                    result.changes.append("key %s -> %s" % (
                        format_key(old, options.key_format) if old is not None else "none", new_text))

        if options.grid and rb.tempos:
            markers = build_grid(rb.tempos, rb.bpm, options.grid_mode, options.offset_ms)
            old = entry.grid
            new = [(round(m.start_ms, 3), m.bpm) for m in markers]
            same = len(old) == len(new) and all(
                abs(o[0] - n[0]) < 0.001 and o[1] is not None and abs(o[1] - n[1]) < 0.0005
                for o, n in zip(old, new))
            if not same:
                entry.set_grid(markers)
                if options.grid_mode == "single" and not is_constant(rb.tempos):
                    result.warnings.append(
                        "variable tempo in Rekordbox; single grid marker will drift "
                        "(try --grid-mode multi)")
                result.changes.append("grid %s -> %s" % (_fmt_grid(old), _fmt_grid(new)))

        if result.changes and options.lock and not entry.locked:
            entry.lock()
            result.changes.append("locked")

        result.status = "updated" if result.changes else "unchanged"

    return results
