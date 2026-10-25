"""File path normalisation used to match Rekordbox tracks to Traktor entries.

Both applications store absolute paths, but in different shapes:

* Rekordbox XML: ``file://localhost/C:/Music/a.mp3`` (Windows) or
  ``file://localhost/Volumes/USB/Music/a.mp3`` (macOS), URL-encoded.
* Traktor NML: ``<LOCATION VOLUME="C:" DIR="/:Music/:" FILE="a.mp3"/>``, where
  VOLUME is a drive letter (Windows) or volume name (macOS) and DIR is
  relative to that volume's root.

We reduce both to a ``(volume, relative_path)`` pair and match primarily on the
relative path, which is the same in both programs as long as the files have not
moved. The volume is only used to break ties.
"""

import re
import unicodedata
from urllib.parse import unquote, urlparse

_DRIVE_RE = re.compile(r"^/?([A-Za-z]):(/.*)?$")


def _norm(s):
    return unicodedata.normalize("NFC", s).casefold()


def normalize_volume(volume):
    if not volume:
        return ""
    return _norm(volume.rstrip(":").rstrip("\\/"))


def location_to_path(location):
    """Convert a Rekordbox ``Location`` URL into a plain absolute path string."""
    if location.startswith("file:"):
        parsed = urlparse(location)
        path = unquote(parsed.path)
    else:
        path = location
    path = path.replace("\\", "/")
    m = _DRIVE_RE.match(path)
    if m:
        return "%s:%s" % (m.group(1).upper(), m.group(2) or "/")
    return path


def split_path(path):
    """Split an absolute path into ``(volume, relative_path)``.

    ``relative_path`` always starts with ``/``. ``volume`` is the drive letter
    on Windows, the volume name for ``/Volumes/<name>/...`` on macOS, and ``""``
    for the macOS system volume.
    """
    path = path.replace("\\", "/")
    m = _DRIVE_RE.match(path)
    if m:
        return m.group(1).upper() + ":", m.group(2) or "/"
    if path.startswith("/Volumes/"):
        rest = path[len("/Volumes/"):]
        volume, _, rel = rest.partition("/")
        return volume, "/" + rel
    return "", path


def traktor_location_path(volume, dir_, file_):
    """Build ``(volume, relative_path)`` from a Traktor LOCATION element."""
    rel_dir = (dir_ or "").replace("/:", "/")
    if not rel_dir.startswith("/"):
        rel_dir = "/" + rel_dir
    if not rel_dir.endswith("/"):
        rel_dir += "/"
    return volume or "", rel_dir + (file_ or "")


def match_key(rel_path):
    return _norm(rel_path)


def basename_key(rel_path):
    return _norm(rel_path.rsplit("/", 1)[-1])


def apply_path_maps(path, path_maps):
    """Apply ``(old_prefix, new_prefix)`` rewrites; the first matching one wins."""
    for old, new in path_maps:
        if path.startswith(old) or _norm(path).startswith(_norm(old)):
            return new + path[len(old):]
    return path
