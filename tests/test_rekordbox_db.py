import contextlib
import io
import os
import shutil
import struct
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from rb2traktor import cli
from rb2traktor.rekordbox_db import DatabaseUnavailable, segments_from_beats

try:
    import pyrekordbox  # noqa: F401
    from pyrekordbox.masterdb import models
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
except ImportError:
    models = None

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")
NML = os.path.join(FIXTURES, "collection.nml")
RB_XML = os.path.join(FIXTURES, "rekordbox.xml")


class SegmentTests(unittest.TestCase):
    def test_constant(self):
        segs = segments_from_beats([3, 4, 1, 2], [128.0] * 4, [0.12, 0.589, 1.058, 1.527])
        self.assertEqual(len(segs), 1)
        self.assertEqual((segs[0].start, segs[0].bpm, segs[0].beat), (0.12, 128.0, 3))

    def test_tempo_change(self):
        segs = segments_from_beats([1, 2, 3, 4], [120.0, 120.0, 122.0, 122.0],
                                   [0.0, 0.5, 1.0, 1.49])
        self.assertEqual([(s.start, s.bpm, s.beat) for s in segs],
                         [(0.0, 120.0, 1), (1.0, 122.0, 3)])

    def test_empty(self):
        self.assertEqual(segments_from_beats([], [], []), [])


def _anlz_bytes(entries):
    """Minimal ANLZ .DAT file holding only a PQTZ (beat grid) tag."""
    body = struct.pack(">III", 0, 0x80000, len(entries))
    body += b"".join(struct.pack(">HHI", beat, tempo, time) for beat, tempo, time in entries)
    tag = b"PQTZ" + struct.pack(">II", 24, 12 + len(body)) + body
    return b"PMAI" + struct.pack(">II", 28, 28 + len(tag)) + b"\0" * 16 + tag


def _row(model, **values):
    """Build a row, filling NOT NULL columns we don't care about with blanks."""
    for col in model.__table__.columns:
        if col.key in values or col.nullable or col.primary_key or col.default is not None:
            continue
        try:
            values[col.key] = 0 if issubclass(col.type.python_type, (int, float)) else ""
        except NotImplementedError:
            values[col.key] = ""
    return model(**values)


@unittest.skipIf(models is None, "pyrekordbox not installed")
class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.db = self.tmp / "master.db"
        engine = create_engine("sqlite:///%s" % self.db)
        models.Base.metadata.create_all(engine)
        anlz = "/PIONEER/USBANLZ/ab/cd/ANLZ0000.DAT"
        with Session(engine) as s:
            s.add(_row(models.DjmdKey, ID="k1", ScaleName="Am"))
            s.add(_row(models.DjmdArtist, ID="a1", Name="Artist A"))
            s.add(_row(models.DjmdContent, ID="1", Title="Constant", ArtistID="a1", KeyID="k1",
                       BPM=12800, FolderPath="/Users/dj/Music/Constant Track.mp3",
                       AnalysisDataPath=anlz))
            s.add(_row(models.DjmdContent, ID="2", Title="Deleted", BPM=9000,
                       FolderPath="/Users/dj/Music/deleted.mp3", rb_local_deleted=1))
            s.add(_row(models.DjmdContent, ID="3", Title="No analysis", BPM=17400,
                       FolderPath="C:\\Music\\dnb.mp3"))
            s.commit()
        engine.dispose()
        grid_dir = self.tmp / "share" / "PIONEER" / "USBANLZ" / "ab" / "cd"
        grid_dir.mkdir(parents=True)
        (grid_dir / "ANLZ0000.DAT").write_bytes(
            _anlz_bytes([(3, 12800, 120), (4, 12800, 589), (1, 12800, 1058)]))

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_read(self):
        from rb2traktor.rekordbox_db import read_rekordbox_db
        tracks, warnings = read_rekordbox_db(self.db, unlock=False)
        self.assertEqual(warnings, [])
        by_name = {t.name: t for t in tracks}
        self.assertEqual(sorted(by_name), ["Constant", "No analysis"])

        const = by_name["Constant"]
        self.assertEqual(const.artist, "Artist A")
        self.assertEqual(const.path, "/Users/dj/Music/Constant Track.mp3")
        self.assertEqual(const.bpm, 128.0)
        self.assertEqual(const.key, 21)
        self.assertEqual([(t.start, t.bpm, t.beat) for t in const.tempos], [(0.12, 128.0, 3)])

        other = by_name["No analysis"]
        self.assertEqual(other.path, "C:/Music/dnb.mp3")
        self.assertIsNone(other.key)
        self.assertEqual(other.tempos, [])


class SourceSelectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.nml = os.path.join(self.tmp, "collection.nml")
        shutil.copy(NML, self.nml)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def _run(self, argv, env=None):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, env or {}), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_auto_falls_back_to_default_xml(self):
        unavailable = DatabaseUnavailable("pyrekordbox missing")
        with mock.patch.object(cli, "read_rekordbox_db", side_effect=unavailable):
            code, out, _ = self._run([self.nml, "-n"], {"RB2TRAKTOR_XML": RB_XML})
        self.assertEqual(code, 0)
        self.assertIn("Rekordbox database not used (pyrekordbox missing)", out)
        self.assertIn("Using Rekordbox XML: %s" % RB_XML, out)

    def test_auto_without_any_source(self):
        unavailable = DatabaseUnavailable("pyrekordbox missing")
        missing = os.path.join(self.tmp, "nope.xml")
        with mock.patch.object(cli, "read_rekordbox_db", side_effect=unavailable):
            code, _, err = self._run([self.nml, "-n"], {"RB2TRAKTOR_XML": missing})
        self.assertEqual(code, 2)
        self.assertIn("no Rekordbox source available", err)

    def test_auto_prefers_database(self):
        from rb2traktor.rekordbox import read_rekordbox_xml
        tracks = read_rekordbox_xml(RB_XML)
        with mock.patch.object(cli, "read_rekordbox_db", return_value=(tracks, [])) as db:
            code, out, _ = self._run([self.nml, "-n"])
        self.assertEqual(code, 0)
        db.assert_called_once_with(None, None)
        self.assertIn("Read 4 tracks from the Rekordbox database", out)

    def test_explicit_db_path(self):
        with mock.patch.object(cli, "read_rekordbox_db", return_value=([], [])) as db:
            code, _, _ = self._run(["/x/master.db", self.nml, "-n", "--db-key", "k"])
        self.assertEqual(code, 0)
        db.assert_called_once_with("/x/master.db", "k")

    def test_several_traktor_collections(self):
        old = os.path.join(self.tmp, "Traktor 3.11.1", "collection.nml")
        with mock.patch.object(cli, "find_collections", return_value=[self.nml, old]):
            code, out, _ = self._run([RB_XML, "-n"])
        self.assertEqual(code, 0)
        self.assertIn("Using Traktor collection: %s" % self.nml, out)
        self.assertIn("also found %s" % old, out)

    def test_stale_xml_warning(self):
        xml = os.path.join(self.tmp, "rekordbox.xml")
        shutil.copy(RB_XML, xml)
        os.utime(xml, (1000, 1000))
        fake_db = Path(self.tmp) / "master.db"
        fake_db.write_text("")
        with mock.patch.object(cli, "default_db_paths", return_value=[fake_db]):
            code, _, err = self._run([xml, self.nml, "-n"])
        self.assertEqual(code, 0)
        self.assertIn("is older than your Rekordbox library", err)


if __name__ == "__main__":
    unittest.main()
