"""Reading, editing and writing Traktor ``collection.nml`` files."""

import glob
import os
import time
import xml.etree.ElementTree as ET

from .keys import format_key
from .paths import traktor_location_path

GRID_CUE_TYPE = "4"

# CUE_V2 TYPE for each Rekordbox cue type (4 is the grid marker).
CUE_TYPES = {"cue": "0", "fade-in": "1", "fade-out": "2", "load": "3", "loop": "5"}
# Traktor's name for a cue without one.
UNNAMED_CUE = "n.n."

# Canonical order of ENTRY children as written by Traktor. New elements are
# inserted after the last existing element that precedes them in this list.
_ENTRY_ORDER = [
    "LOCATION", "ALBUM", "MODIFICATION_INFO", "INFO", "TEMPO", "LOUDNESS",
    "MUSICAL_KEY", "LOOPINFO", "CUE_V2", "STEMS",
]

_XML_DECLARATION = '<?xml version="1.0" encoding="UTF-8" standalone="no" ?>\n'


class TraktorEntry:
    def __init__(self, element):
        self.element = element
        loc = element.find("LOCATION")
        if loc is None:
            self.volume, self.rel_path = "", ""
        else:
            self.volume, self.rel_path = traktor_location_path(
                loc.get("VOLUME"), loc.get("DIR"), loc.get("FILE"))

    @property
    def label(self):
        artist, title = self.element.get("ARTIST", ""), self.element.get("TITLE", "")
        if artist and title:
            return "%s - %s" % (artist, title)
        return title or self.rel_path

    @property
    def locked(self):
        return self.element.get("LOCK") == "1"

    @property
    def bpm(self):
        tempo = self.element.find("TEMPO")
        try:
            return float(tempo.get("BPM")) if tempo is not None else None
        except (TypeError, ValueError):
            return None

    @property
    def key(self):
        mk = self.element.find("MUSICAL_KEY")
        try:
            return int(mk.get("VALUE")) if mk is not None else None
        except (TypeError, ValueError):
            return None

    @property
    def grid(self):
        """Existing grid markers as ``(start_ms, bpm or None)`` tuples."""
        markers = []
        for cue in self.element.findall("CUE_V2"):
            if cue.get("TYPE") != GRID_CUE_TYPE:
                continue
            grid = cue.find("GRID")
            bpm = float(grid.get("BPM")) if grid is not None and grid.get("BPM") else None
            markers.append((float(cue.get("START", "0")), bpm))
        return markers

    @property
    def hotcues(self):
        """Existing hot cues as ``{slot: CUE_V2 element}``, grid markers excluded."""
        cues = {}
        for cue in self.element.findall("CUE_V2"):
            if cue.get("TYPE") == GRID_CUE_TYPE:
                continue
            try:
                slot = int(cue.get("HOTCUE", "-1"))
            except ValueError:
                continue
            if slot >= 0:
                cues[slot] = cue
        return cues

    # -- mutation --------------------------------------------------------

    def _insert(self, child):
        """Insert ``child`` into the entry respecting Traktor's element order."""
        entry = self.element
        rank = _ENTRY_ORDER.index(child.tag) if child.tag in _ENTRY_ORDER else len(_ENTRY_ORDER)
        index = 0
        for i, existing in enumerate(list(entry)):
            r = _ENTRY_ORDER.index(existing.tag) if existing.tag in _ENTRY_ORDER else len(_ENTRY_ORDER)
            if r <= rank:
                index = i + 1
        entry.insert(index, child)
        return child

    def _get_or_create(self, tag):
        el = self.element.find(tag)
        if el is None:
            el = self._insert(ET.Element(tag))
        return el

    def set_bpm(self, bpm):
        tempo = self._get_or_create("TEMPO")
        tempo.set("BPM", "%.6f" % bpm)
        tempo.set("BPM_QUALITY", "100.000000")

    def set_key(self, value, key_format="openkey"):
        mk = self._get_or_create("MUSICAL_KEY")
        mk.set("VALUE", str(value))
        info = self._get_or_create("INFO")
        info.set("KEY", format_key(value, key_format))

    def set_grid(self, markers):
        entry = self.element
        for cue in [c for c in entry.findall("CUE_V2") if c.get("TYPE") == GRID_CUE_TYPE]:
            entry.remove(cue)
        for marker in markers:
            cue = ET.Element("CUE_V2", {
                "NAME": "Beat Marker",
                "DISPL_ORDER": "0",
                "TYPE": GRID_CUE_TYPE,
                "START": "%.6f" % marker.start_ms,
                "LEN": "0.000000",
                "REPEATS": "-1",
                "HOTCUE": "-1",
            })
            ET.SubElement(cue, "GRID", {"BPM": "%.6f" % marker.bpm})
            self._insert(cue)

    def set_hotcue(self, attrs):
        """Put a hot cue (CUE_V2 attributes, see ``hotcue_attrs``) in its slot.

        Traktor often parks the AutoGrid marker on hot cue 1; a grid marker
        in the slot keeps its place in the grid but stops being a hot cue.
        """
        entry, slot = self.element, attrs["HOTCUE"]
        for cue in entry.findall("CUE_V2"):
            if cue.get("HOTCUE") != slot:
                continue
            if cue.get("TYPE") == GRID_CUE_TYPE:
                cue.set("HOTCUE", "-1")
            else:
                entry.remove(cue)
        self._insert(ET.Element("CUE_V2", attrs))

    def lock(self):
        self.element.set("LOCK", "1")
        self.element.set("LOCK_MODIFICATION_TIME", time.strftime("%Y-%m-%dT%H:%M:%S"))


def hotcue_attrs(cue, offset_ms=0.0):
    """CUE_V2 attributes for a Rekordbox ``HotCue``."""
    length = (cue.end - cue.start) * 1000.0 if cue.end is not None else 0.0
    return {
        "NAME": cue.name or UNNAMED_CUE,
        "DISPL_ORDER": "0",
        "TYPE": CUE_TYPES.get(cue.type, "0"),
        "START": "%.6f" % (cue.start * 1000.0 + offset_ms),
        "LEN": "%.6f" % length,
        "REPEATS": "-1",
        "HOTCUE": str(cue.slot),
    }


def same_hotcue(element, attrs):
    """True if an existing CUE_V2 already matches ``attrs`` (to the millisecond)."""
    def num(value):
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def name(value):
        return "" if value in (None, UNNAMED_CUE) else value

    for key in ("START", "LEN"):
        a, b = num(element.get(key)), num(attrs[key])
        if a is None or abs(a - b) >= 0.5:
            return False
    return (element.get("TYPE") == attrs["TYPE"]
            and name(element.get("NAME")) == name(attrs["NAME"]))


class TraktorCollection:
    def __init__(self, path):
        self.path = path
        self.tree = ET.parse(path)
        root = self.tree.getroot()
        if root.tag != "NML":
            raise ValueError("%s does not look like a Traktor NML file" % path)
        collection = root.find("COLLECTION")
        if collection is None:
            raise ValueError("%s has no COLLECTION element" % path)
        self.entries = [TraktorEntry(e) for e in collection.findall("ENTRY")]

    def write(self, path):
        root = self.tree.getroot()
        collection = root.find("COLLECTION")
        collection.set("ENTRIES", str(len(collection.findall("ENTRY"))))
        data = _XML_DECLARATION + ET.tostring(root, encoding="unicode", short_empty_elements=False)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="") as fh:
            fh.write(data)
        os.replace(tmp, path)


def find_collections():
    """Return candidate Traktor collection.nml paths, newest version first.

    Raises PermissionError when the folder exists but can't be listed, which on
    macOS means the terminal hasn't been granted access to ~/Documents (glob
    would silently report nothing).
    """
    documents = os.path.join(os.path.expanduser("~"), "Documents")
    if os.path.isdir(documents):
        os.listdir(documents)
    ni_dir = os.path.join(documents, "Native Instruments")
    found = glob.glob(os.path.join(glob.escape(ni_dir), "Traktor*", "collection.nml"))
    return sorted(found, key=os.path.getmtime, reverse=True)
