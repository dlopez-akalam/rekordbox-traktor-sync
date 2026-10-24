"""Convert Rekordbox tempo segments into Traktor grid markers."""

from dataclasses import dataclass

BPM_EPSILON = 0.005


@dataclass
class GridMarker:
    start_ms: float
    bpm: float


def _beats_per_bar(meter):
    try:
        return max(1, int(str(meter).split("/")[0]))
    except ValueError:
        return 4


def _downbeat(segment, limit=None):
    """Return the start (seconds) of the first downbeat at or after the segment start.

    Rekordbox may start a tempo segment on any beat of the bar (``Battito``),
    while Traktor counts bars from the grid marker, so we move the marker
    forward to the next "1". If that would cross ``limit`` (the next segment),
    the unshifted start is kept.
    """
    bpb = _beats_per_bar(segment.meter)
    beats = (1 - segment.beat) % bpb
    start = segment.start + beats * 60.0 / segment.bpm
    if limit is not None and start >= limit:
        return segment.start
    return start


def is_constant(tempos):
    return all(abs(t.bpm - tempos[0].bpm) < BPM_EPSILON for t in tempos)


def build_grid(tempos, average_bpm=None, mode="single", offset_ms=0.0):
    """Build Traktor grid markers from Rekordbox tempo segments.

    ``mode="single"``: one marker on the first downbeat. For variable-tempo
    tracks the average BPM is used, so the grid will drift.
    ``mode="multi"``: one marker per tempo change (Traktor 3+ stores a BPM
    per grid marker).
    """
    if not tempos:
        return []

    if mode == "single" or is_constant(tempos):
        first = tempos[0]
        bpm = first.bpm if is_constant(tempos) else (average_bpm or first.bpm)
        return [GridMarker(_downbeat(first) * 1000.0 + offset_ms, bpm)]

    if mode != "multi":
        raise ValueError("unknown grid mode: %r" % mode)

    # Collapse runs of segments sharing the same tempo.
    kept = [tempos[0]]
    for seg in tempos[1:]:
        if abs(seg.bpm - kept[-1].bpm) >= BPM_EPSILON:
            kept.append(seg)

    markers = []
    for i, seg in enumerate(kept):
        limit = kept[i + 1].start if i + 1 < len(kept) else None
        markers.append(GridMarker(_downbeat(seg, limit) * 1000.0 + offset_ms, seg.bpm))
    return markers
