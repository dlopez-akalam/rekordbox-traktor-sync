"""Reader for Rekordbox XML collection exports (File > Export Collection in xml format)."""

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

from .keys import parse_key
from .paths import location_to_path


@dataclass
class TempoSegment:
    start: float  # seconds
    bpm: float
    beat: int = 1  # position of this beat within the bar (1..4)
    meter: str = "4/4"


# POSITION_MARK Type in Rekordbox XML exports.
CUE_TYPES = {0: "cue", 1: "fade-in", 2: "fade-out", 3: "load", 4: "loop"}

HOTCUE_LETTERS = "ABCDEFGH"


@dataclass
class HotCue:
    slot: int  # 0..7 = hot cue A..H
    start: float  # seconds
    end: float = None  # seconds, hot loops only
    name: str = ""
    type: str = "cue"  # see CUE_TYPES

    @property
    def letter(self):
        return HOTCUE_LETTERS[self.slot]


@dataclass
class RekordboxTrack:
    track_id: str
    name: str
    artist: str
    path: str
    bpm: float = None
    key_text: str = ""
    key: int = None  # Traktor key value, see keys.py
    tempos: list = field(default_factory=list)
    hotcues: list = field(default_factory=list)

    @property
    def label(self):
        if self.artist and self.name:
            return "%s - %s" % (self.artist, self.name)
        return self.name or self.path


def _float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def read_rekordbox_xml(path):
    tree = ET.parse(path)
    root = tree.getroot()
    if root.tag != "DJ_PLAYLISTS":
        raise ValueError("%s does not look like a Rekordbox XML export" % path)
    collection = root.find("COLLECTION")
    if collection is None:
        raise ValueError("%s has no COLLECTION element" % path)

    tracks = []
    for el in collection.findall("TRACK"):
        location = el.get("Location")
        if not location:
            continue
        tempos = []
        for t in el.findall("TEMPO"):
            start, bpm = _float(t.get("Inizio")), _float(t.get("Bpm"))
            if start is None or not bpm:
                continue
            try:
                beat = int(t.get("Battito", "1"))
            except ValueError:
                beat = 1
            tempos.append(TempoSegment(start, bpm, beat, t.get("Metro", "4/4")))
        tempos.sort(key=lambda s: s.start)

        hotcues = {}
        for mark in el.findall("POSITION_MARK"):
            try:
                slot = int(mark.get("Num", "-1"))
                cue_type = CUE_TYPES.get(int(mark.get("Type", "0")), "cue")
            except ValueError:
                continue
            start = _float(mark.get("Start"))
            # Num -1 is a memory cue; Traktor has 8 hot cues, like Rekordbox.
            if start is None or not 0 <= slot < len(HOTCUE_LETTERS):
                continue
            end = _float(mark.get("End"))
            if cue_type == "loop" and (end is None or end <= start):
                cue_type, end = "cue", None
            elif cue_type != "loop":
                end = None
            hotcues[slot] = HotCue(slot, start, end, mark.get("Name", "") or "", cue_type)

        bpm = _float(el.get("AverageBpm"))
        key_text = el.get("Tonality", "") or ""
        tracks.append(RekordboxTrack(
            track_id=el.get("TrackID", ""),
            name=el.get("Name", ""),
            artist=el.get("Artist", ""),
            path=location_to_path(location),
            bpm=bpm if bpm else None,
            key_text=key_text,
            key=parse_key(key_text),
            tempos=tempos,
            hotcues=[hotcues[k] for k in sorted(hotcues)],
        ))
    return tracks
