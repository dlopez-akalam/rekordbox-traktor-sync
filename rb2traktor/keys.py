"""Musical key parsing and conversion between Rekordbox and Traktor notations.

Internally a key is represented by Traktor's MUSICAL_KEY value:
    0..11  -> major keys, pitch class C, C#, D, ... B
    12..23 -> minor keys, pitch class C, C#, D, ... B (value - 12)
"""

import re

_PITCH_CLASSES = {
    "C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11,
}

_MAJOR_NAMES = ["C", "Db", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]
_MINOR_NAMES = ["Cm", "C#m", "Dm", "Ebm", "Em", "Fm", "F#m", "Gm", "G#m", "Am", "Bbm", "Bm"]

_MUSICAL_RE = re.compile(
    r"^([A-G])\s*([#b♯♭]?)\s*(m|min|minor|maj|major)?$", re.IGNORECASE
)
_CAMELOT_RE = re.compile(r"^(1[0-2]|[1-9])\s*([AB])$", re.IGNORECASE)
_OPENKEY_RE = re.compile(r"^(1[0-2]|[1-9])\s*([dm])$", re.IGNORECASE)

KEY_FORMATS = ("openkey", "musical", "camelot")


def _value(pitch_class, minor):
    return (pitch_class % 12) + (12 if minor else 0)


def parse_key(text):
    """Parse a key string (musical, Camelot or Open Key) into a Traktor key value.

    Returns None if the string is empty or not recognised.
    """
    if not text:
        return None
    text = text.strip()
    if not text:
        return None

    m = _CAMELOT_RE.match(text)
    if m:
        n, letter = int(m.group(1)), m.group(2).upper()
        # 8B = C major, 8A = A minor; each step adds a fifth (7 semitones).
        pc = (n - 8) * 7
        if letter == "A":
            return _value(pc + 9, minor=True)
        return _value(pc, minor=False)

    m = _OPENKEY_RE.match(text)
    if m:
        n, letter = int(m.group(1)), m.group(2).lower()
        # 1d = C major, 1m = A minor.
        pc = (n - 1) * 7
        if letter == "m":
            return _value(pc + 9, minor=True)
        return _value(pc, minor=False)

    m = _MUSICAL_RE.match(text)
    if m:
        note, accidental, quality = m.group(1).upper(), m.group(2), (m.group(3) or "")
        pc = _PITCH_CLASSES[note]
        if accidental in ("#", "♯"):
            pc += 1
        elif accidental in ("b", "♭"):
            pc -= 1
        minor = quality.lower() in ("m", "min", "minor")
        return _value(pc, minor)

    return None


def format_key(value, fmt="openkey"):
    """Format a Traktor key value (0..23) as text in the requested notation."""
    if value is None:
        return ""
    minor = value >= 12
    pc = value % 12
    if fmt == "musical":
        return (_MINOR_NAMES if minor else _MAJOR_NAMES)[pc]
    # Position on the circle of fifths: C major / A minor are position 0.
    base = (pc - 9) % 12 if minor else pc
    steps = (base * 7) % 12  # 7 is its own inverse mod 12
    if fmt == "openkey":
        return "%d%s" % (steps + 1, "m" if minor else "d")
    if fmt == "camelot":
        return "%d%s" % ((steps + 7) % 12 + 1, "A" if minor else "B")
    raise ValueError("unknown key format: %r" % fmt)
