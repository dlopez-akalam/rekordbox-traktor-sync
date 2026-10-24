import contextlib
import io
import os
import shutil
import tempfile
import unittest

from rb2traktor.cli import main
from rb2traktor.grid import build_grid
from rb2traktor.keys import format_key, parse_key
from rb2traktor.paths import location_to_path, split_path, traktor_location_path
from rb2traktor.rekordbox import TempoSegment, read_rekordbox_xml
from rb2traktor.sync import SyncOptions, sync
from rb2traktor.traktor import TraktorCollection

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")
RB_XML = os.path.join(FIXTURES, "rekordbox.xml")
NML = os.path.join(FIXTURES, "collection.nml")


class KeyTests(unittest.TestCase):
    def test_musical(self):
        self.assertEqual(parse_key("C"), 0)
        self.assertEqual(parse_key("Am"), 21)
        self.assertEqual(parse_key("F#m"), 18)
        self.assertEqual(parse_key("Gbm"), 18)
        self.assertEqual(parse_key("Bbm"), 22)
        self.assertEqual(parse_key("Bm"), 23)
        self.assertEqual(parse_key("Eb"), 3)
        self.assertEqual(parse_key("Cb"), 11)
        self.assertIsNone(parse_key(""))
        self.assertIsNone(parse_key("xyz"))

    def test_camelot_and_openkey(self):
        self.assertEqual(parse_key("8B"), 0)    # C
        self.assertEqual(parse_key("8A"), 21)   # Am
        self.assertEqual(parse_key("1B"), 11)   # B
        self.assertEqual(parse_key("1A"), 20)   # G#m
        self.assertEqual(parse_key("1d"), 0)
        self.assertEqual(parse_key("1m"), 21)
        self.assertEqual(parse_key("6m"), 20)   # G#m
        self.assertEqual(parse_key("12d"), 5)   # F

    def test_round_trip(self):
        for value in range(24):
            for fmt in ("openkey", "musical", "camelot"):
                self.assertEqual(parse_key(format_key(value, fmt)), value, (value, fmt))

    def test_format(self):
        self.assertEqual(format_key(21, "openkey"), "1m")
        self.assertEqual(format_key(21, "camelot"), "8A")
        self.assertEqual(format_key(21, "musical"), "Am")
        self.assertEqual(format_key(0, "camelot"), "8B")


class PathTests(unittest.TestCase):
    def test_windows(self):
        path = location_to_path("file://localhost/C:/My%20Music/a%20b.mp3")
        self.assertEqual(path, "C:/My Music/a b.mp3")
        self.assertEqual(split_path(path), ("C:", "/My Music/a b.mp3"))

    def test_mac(self):
        self.assertEqual(split_path("/Volumes/USB/x/a.mp3"), ("USB", "/x/a.mp3"))
        self.assertEqual(split_path("/Users/me/a.mp3"), ("", "/Users/me/a.mp3"))

    def test_traktor(self):
        self.assertEqual(traktor_location_path("C:", "/:Music/:House/:", "a.mp3"),
                         ("C:", "/Music/House/a.mp3"))


class GridTests(unittest.TestCase):
    def test_moves_to_downbeat(self):
        # Battito=3 -> two beats until the next "1": 0.12 + 2 * 60/128 s
        markers = build_grid([TempoSegment(0.12, 128.0, 3)])
        self.assertEqual(len(markers), 1)
        self.assertAlmostEqual(markers[0].start_ms, 120 + 2 * 60000 / 128)
        self.assertEqual(markers[0].bpm, 128.0)

    def test_offset(self):
        markers = build_grid([TempoSegment(0.1, 120.0, 1)], offset_ms=-26)
        self.assertAlmostEqual(markers[0].start_ms, 74)

    def test_variable(self):
        tempos = [TempoSegment(0.0, 120.0), TempoSegment(2.0, 120.0), TempoSegment(4.0, 122.0)]
        single = build_grid(tempos, average_bpm=121.0)
        self.assertEqual([(m.start_ms, m.bpm) for m in single], [(0.0, 121.0)])
        multi = build_grid(tempos, average_bpm=121.0, mode="multi")
        self.assertEqual([(m.start_ms, m.bpm) for m in multi], [(0.0, 120.0), (4000.0, 122.0)])


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.nml = os.path.join(self.tmp, "collection.nml")
        shutil.copy(NML, self.nml)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def _sync(self, **kwargs):
        collection = TraktorCollection(self.nml)
        results = sync(read_rekordbox_xml(RB_XML), collection, SyncOptions(**kwargs))
        return collection, {r.rb.name: r for r in results}

    def test_sync(self):
        collection, results = self._sync(overwrite=frozenset({"bpm", "key", "grid"}))
        self.assertEqual(results["Constant"].status, "updated")
        self.assertEqual(results["Variable"].status, "updated")
        self.assertEqual(results["Moved"].status, "updated")  # filename fallback
        self.assertEqual(results["Missing"].status, "unmatched")
        self.assertTrue(results["Variable"].warnings)

        collection.write(self.nml)
        reread = TraktorCollection(self.nml)
        by_title = {e.element.get("TITLE"): e for e in reread.entries}

        const = by_title["Constant"]
        self.assertAlmostEqual(const.bpm, 128.0)
        self.assertEqual(const.key, 21)
        self.assertEqual(const.element.find("INFO").get("KEY"), "1m")
        self.assertEqual(len(const.grid), 1)
        self.assertAlmostEqual(const.grid[0][0], 120 + 2 * 60000 / 128, places=3)
        self.assertTrue(const.locked)
        # Non-grid cues are preserved.
        names = [c.get("NAME") for c in const.element.findall("CUE_V2")]
        self.assertEqual(names, ["Drop", "Beat Marker"])

        var = by_title["Variable"]
        tags = [c.tag for c in var.element]
        self.assertEqual(tags, ["LOCATION", "INFO", "TEMPO", "MUSICAL_KEY", "CUE_V2"])
        self.assertEqual(var.key, 21)  # 8A

        moved = by_title["Moved"]
        self.assertEqual(moved.key, 18)

        # Running again changes nothing.
        _, results = self._sync(overwrite=frozenset({"bpm", "key", "grid"}))
        self.assertEqual(results["Constant"].status, "unchanged")
        self.assertEqual(results["Variable"].status, "unchanged")

    def test_fill_only_by_default(self):
        collection, results = self._sync()
        const = results["Constant"]
        self.assertEqual(const.status, "kept")
        self.assertEqual(const.kept, ["bpm", "key", "grid"])
        self.assertAlmostEqual(const.entry.bpm, 127.99)
        self.assertEqual(const.entry.key, 0)
        self.assertEqual(const.entry.grid, [(50.0, None)])
        self.assertFalse(const.entry.locked)
        # Tracks with no metadata in Traktor are still filled in.
        self.assertEqual(results["Variable"].status, "updated")
        self.assertAlmostEqual(results["Variable"].entry.bpm, 121.0)

    def test_overwrite_selected_fields(self):
        _, results = self._sync(overwrite=frozenset({"bpm"}))
        const = results["Constant"]
        self.assertEqual(const.status, "updated")
        self.assertEqual(const.kept, ["key", "grid"])
        self.assertAlmostEqual(const.entry.bpm, 128.0)
        self.assertEqual(const.entry.key, 0)
        self.assertEqual(const.entry.grid, [(50.0, None)])

    def test_skip_locked_and_no_filename(self):
        _, results = self._sync(skip_locked=True, match_filename=False)
        self.assertEqual(results["Moved"].status, "unmatched")
        _, results = self._sync(skip_locked=True)
        self.assertEqual(results["Moved"].status, "locked")

    def test_path_map(self):
        _, results = self._sync(match_filename=False,
                                path_maps=[("/Users/dj/Old", "/Users/dj/Music")])
        self.assertEqual(results["Moved"].status, "updated")

    def test_cli(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main([RB_XML, self.nml, "--dry-run"]), 0)
        self.assertIn("updated:   2", out.getvalue())
        self.assertIn("kept existing Traktor values: 1", out.getvalue())

        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main([RB_XML, self.nml, "--dry-run", "--overwrite", "all"]), 0)
        self.assertIn("updated:   3", out.getvalue())
        with open(self.nml) as fh, open(NML) as orig:
            self.assertEqual(fh.read(), orig.read())

        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main([RB_XML, self.nml, "--overwrite", "key", "--overwrite", "grid"]), 0)
        backups = [f for f in os.listdir(self.tmp) if f.endswith(".bak")]
        self.assertEqual(len(backups), 1)
        with open(self.nml) as fh:
            self.assertTrue(fh.read().startswith('<?xml version="1.0" encoding="UTF-8" standalone="no" ?>'))


if __name__ == "__main__":
    unittest.main()
