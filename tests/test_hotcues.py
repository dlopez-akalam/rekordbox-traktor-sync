import os
import shutil
import tempfile
import unittest
from types import SimpleNamespace

from rb2traktor.rekordbox import read_rekordbox_xml
from rb2traktor.rekordbox_db import hotcues_from_rows
from rb2traktor.sync import SyncOptions, sync
from rb2traktor.traktor import TraktorCollection

REKORDBOX_XML = """<?xml version="1.0" encoding="UTF-8"?>
<DJ_PLAYLISTS Version="1.0.0">
  <COLLECTION Entries="1">
    <TRACK TrackID="1" Name="Cues" Artist="Artist" AverageBpm="128.00" Tonality="Am"
           Location="file://localhost/Users/dj/Music/cues.mp3">
      <TEMPO Inizio="0.050" Bpm="128.00" Metro="4/4" Battito="1"/>
      <POSITION_MARK Name="Intro" Type="0" Start="0.500" Num="0"/>
      <POSITION_MARK Name="" Type="0" Start="45.000" Num="1"/>
      <POSITION_MARK Name="Build" Type="4" Start="60.000" End="67.500" Num="3"/>
      <POSITION_MARK Name="Memory" Type="0" Start="10.000" Num="-1"/>
    </TRACK>
  </COLLECTION>
</DJ_PLAYLISTS>
"""

TRAKTOR_NML = """<?xml version="1.0" encoding="UTF-8" standalone="no" ?>
<NML VERSION="19"><COLLECTION ENTRIES="1">
<ENTRY TITLE="Cues" ARTIST="Artist"><LOCATION DIR="/:Users/:dj/:Music/:" FILE="cues.mp3" VOLUME="Macintosh HD"></LOCATION>
<CUE_V2 NAME="AutoGrid" DISPL_ORDER="0" TYPE="4" START="50.000000" LEN="0.000000" REPEATS="-1" HOTCUE="0"></CUE_V2>
<CUE_V2 NAME="Drop" DISPL_ORDER="0" TYPE="0" START="30000.000000" LEN="0.000000" REPEATS="-1" HOTCUE="1"></CUE_V2>
<CUE_V2 NAME="Mine" DISPL_ORDER="0" TYPE="0" START="90000.000000" LEN="0.000000" REPEATS="-1" HOTCUE="7"></CUE_V2>
</ENTRY>
</COLLECTION></NML>
"""

# Only look at hot cues unless a test says otherwise.
CUES_ONLY = dict(bpm=False, key=False, grid=False)


class HotCueTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.xml = os.path.join(self.tmp, "rekordbox.xml")
        self.nml = os.path.join(self.tmp, "collection.nml")
        with open(self.xml, "w") as fh:
            fh.write(REKORDBOX_XML)
        with open(self.nml, "w") as fh:
            fh.write(TRAKTOR_NML)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def _sync(self, **kwargs):
        collection = TraktorCollection(self.nml)
        result = sync(read_rekordbox_xml(self.xml), collection, SyncOptions(**kwargs))[0]
        return collection, result

    def _cues(self, entry):
        return {(c.get("HOTCUE"), c.get("TYPE")): c for c in entry.element.findall("CUE_V2")}

    def test_read_xml(self):
        cues = read_rekordbox_xml(self.xml)[0].hotcues
        self.assertEqual([(c.letter, c.type, c.start, c.end, c.name) for c in cues], [
            ("A", "cue", 0.5, None, "Intro"),
            ("B", "cue", 45.0, None, ""),
            ("D", "loop", 60.0, 67.5, "Build"),
        ])

    def test_fill_empty_slots_only(self):
        collection, result = self._sync(**CUES_ONLY)
        self.assertEqual(result.status, "updated")
        self.assertEqual(result.changes, ["hot cue A added", "hot cue D added"])
        self.assertEqual(result.kept, ["hot cue B"])

        cues = self._cues(result.entry)
        # The AutoGrid marker stays in the grid but gives up hot cue 1.
        self.assertEqual(cues[("-1", "4")].get("START"), "50.000000")
        self.assertEqual(cues[("0", "0")].get("NAME"), "Intro")
        self.assertEqual(cues[("0", "0")].get("START"), "500.000000")
        self.assertEqual(cues[("1", "0")].get("NAME"), "Drop")  # kept
        loop = cues[("3", "5")]
        self.assertEqual((loop.get("NAME"), loop.get("START"), loop.get("LEN")),
                         ("Build", "60000.000000", "7500.000000"))
        self.assertEqual(cues[("7", "0")].get("NAME"), "Mine")  # untouched slot
        # Hot cues alone don't lock the track.
        self.assertFalse(result.entry.locked)

        # Written, read back, synced again: nothing left to do.
        collection.write(self.nml)
        _, result = self._sync(**CUES_ONLY)
        self.assertEqual(result.status, "kept")
        self.assertEqual(result.changes, [])

    def test_overwrite(self):
        _, result = self._sync(overwrite=frozenset({"hotcues"}), **CUES_ONLY)
        self.assertIn("hot cue B replaced", result.changes)
        cues = self._cues(result.entry)
        self.assertEqual(cues[("1", "0")].get("NAME"), "n.n.")  # unnamed in Rekordbox
        self.assertEqual(cues[("1", "0")].get("START"), "45000.000000")
        self.assertNotIn("Drop", [c.get("NAME") for c in cues.values()])
        # Slots Rekordbox doesn't use are left alone even when overwriting.
        self.assertEqual(cues[("7", "0")].get("NAME"), "Mine")

    def test_offset_and_disable(self):
        _, result = self._sync(offset_ms=-26, **CUES_ONLY)
        self.assertEqual(self._cues(result.entry)[("0", "0")].get("START"), "474.000000")
        _, result = self._sync(hotcues=False, **CUES_ONLY)
        self.assertEqual(result.status, "unchanged")

    def test_locks_only_for_analysis_changes(self):
        _, result = self._sync()
        self.assertTrue(result.entry.locked)  # BPM, key and grid were written too


class DatabaseRowTests(unittest.TestCase):
    def _row(self, kind, in_ms, out_ms=-1, comment="", content="1", deleted=0):
        return SimpleNamespace(ContentID=content, Kind=kind, InMsec=in_ms, OutMsec=out_ms,
                               Comment=comment, rb_local_deleted=deleted)

    def test_kinds(self):
        rows = [
            self._row(1, 500, comment="Intro"),     # A
            self._row(3, 1000),                     # C
            self._row(4, 2000, 4000),               # memory loop: skipped
            self._row(5, 3000),                     # D (Kind 4 is skipped for hot cues)
            self._row(9, 9000, 12500),              # H, a hot loop
            self._row(0, 7000),                     # memory cue: skipped
            self._row(2, 8000, deleted=1),          # deleted: skipped
            self._row(1, 100, content="2"),
        ]
        cues = hotcues_from_rows(rows)
        self.assertEqual(sorted(cues), ["1", "2"])
        self.assertEqual([(c.letter, c.type, c.start, c.end, c.name) for c in cues["1"]], [
            ("A", "cue", 0.5, None, "Intro"),
            ("C", "cue", 1.0, None, ""),
            ("D", "cue", 3.0, None, ""),
            ("H", "loop", 9.0, 12.5, ""),
        ])


if __name__ == "__main__":
    unittest.main()
