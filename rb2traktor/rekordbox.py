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
        ))
    return tracks
