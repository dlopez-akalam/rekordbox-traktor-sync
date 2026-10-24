"""macOS specifics: volume layout, decomposed Unicode file names, privacy prompts."""

import contextlib
import io
import os
import unicodedata
import unittest
import xml.etree.ElementTree as ET
from unittest import mock

from rb2traktor import cli
from rb2traktor.paths import location_to_path
from rb2traktor.sync import Matcher
from rb2traktor.traktor import TraktorEntry


def _entry(volume, dir_, file_):
    el = ET.Element("ENTRY")
    ET.SubElement(el, "LOCATION", {"VOLUME": volume, "DIR": dir_, "FILE": file_})
    return TraktorEntry(el)


class MacPathTests(unittest.TestCase):
    def test_system_volume(self):
        # Rekordbox omits the system volume, Traktor names it ("Macintosh HD").
        entry = _entry("Macintosh HD", "/:Users/:dj/:Music/:", "a.mp3")
        path = location_to_path("file://localhost/Users/dj/Music/a.mp3")
        self.assertIs(Matcher([entry]).match_path(path)[0], entry)

    def test_external_volume(self):
        entry = _entry("DJ USB", "/:Tracks/:", "a.mp3")
        path = location_to_path("file://localhost/Volumes/DJ%20USB/Tracks/a.mp3")
        self.assertIs(Matcher([entry]).match_path(path)[0], entry)

    def test_decomposed_unicode(self):
        # macOS file systems often hand out NFD names ("e" + combining accent);
        # the other application may have stored the composed (NFC) form.
        name = "Café del Mar.mp3"
        nfd = unicodedata.normalize("NFD", name)
        entry = _entry("Macintosh HD", "/:Users/:dj/:Music/:", name)
        quoted = "".join("%%%02X" % b if b > 127 else chr(b) for b in nfd.encode("utf-8"))
        path = location_to_path("file://localhost/Users/dj/Music/" + quoted.replace(" ", "%20"))
        self.assertNotEqual(path, "/Users/dj/Music/" + name)  # really decomposed
        self.assertIs(Matcher([entry]).match_path(path)[0], entry)


class MacPermissionTests(unittest.TestCase):
    def test_documents_access_hint(self):
        err = io.StringIO()
        denied = PermissionError(1, "Operation not permitted", os.path.expanduser("~/Documents"))
        with mock.patch.object(cli, "find_collections", side_effect=denied), \
                mock.patch.object(cli.sys, "platform", "darwin"), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            self.assertEqual(cli.main(["x.xml"]), 2)
        self.assertIn("Operation not permitted", err.getvalue())
        self.assertIn("Privacy & Security", err.getvalue())

    def test_no_hint_elsewhere(self):
        exc = PermissionError(13, "Permission denied", "/x")
        with mock.patch.object(cli.sys, "platform", "win32"):
            self.assertNotIn("Privacy", cli._os_error(exc))


if __name__ == "__main__":
    unittest.main()
