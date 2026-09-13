from __future__ import annotations

import pathlib
import re
import xml.etree.ElementTree as ET

from ada.config import logger

# GeniE stamps `encoding="ASCII"` on every concept XML it exports, but it writes the
# model's text fields out in whatever Windows ANSI codepage it was running under. One
# object named with a non-ASCII character — `§`, `Ø`, `°` are all common in Norwegian
# yards — is therefore a byte expat is entitled to reject, and a single such byte in a
# 500 kB file used to cost the user the entire conversion. The user cannot fix the
# export: GeniE is what writes the mismatched declaration.
#
# cp1252 is tried before latin-1 because that is the codepage GeniE actually runs
# under on a Western-European Windows; the two differ only in 0x80-0x9F, where cp1252
# has the typographic quotes and dashes that latin-1 renders as control characters.
_DECLARED_ENCODING = re.compile(rb"""(<\?xml[^>]*?encoding\s*=\s*)(["'])[^"']*\2""", re.IGNORECASE)


def _decode_tolerantly(data: bytes) -> tuple[str, str]:
    """Decode XML bytes whose declared encoding we have already stopped trusting.

    UTF-8 is tried first and strictly. A file that really is UTF-8 (or ASCII) but is
    mis-declared as something else must come back byte-for-byte identical — pushing it
    through a single-byte codepage instead would turn valid text into mojibake silently,
    which is worse than the ParseError we are recovering from. Only bytes that cannot be
    valid UTF-8 fall through to the ANSI codepages.
    """
    try:
        # utf-8-sig also strips a BOM, which expat tolerates but str-level parsing does not.
        return data.decode("utf-8-sig"), "utf-8"
    except UnicodeDecodeError:
        pass

    try:
        return data.decode("cp1252"), "cp1252"
    except UnicodeDecodeError:
        # cp1252 leaves five byte values undefined; latin-1 maps all 256, so this
        # cannot raise. The glyph may be a guess, but the byte survives the round trip.
        return data.decode("latin-1"), "latin-1"


def _root_ignoring_declared_encoding(data: bytes) -> tuple[ET.Element, str]:
    """Re-parse `data` on our own decode instead of the one the declaration asks for."""
    text, encoding = _decode_tolerantly(data)

    # Hand expat UTF-8 and say so. Re-encoding alone is not enough: a declaration still
    # reading `ASCII` would make expat reject the very bytes we just recovered. The
    # declaration is rewritten rather than stripped so `version`/`standalone` survive
    # as GeniE wrote them. A file with no declaration at all needs no substitution —
    # undeclared bytes default to UTF-8, which is what we are handing over.
    utf8 = _DECLARED_ENCODING.sub(rb'\g<1>"UTF-8"', text.encode("utf-8"), count=1)
    return ET.fromstring(utf8), encoding


def _recover(data: bytes, origin: str, strict_error: ET.ParseError) -> ET.Element:
    try:
        root, encoding = _root_ignoring_declared_encoding(data)
    except ET.ParseError:
        # Re-decoding cannot explain this one, so the file is broken for real
        # (truncated, unbalanced tags, stray `&`). Surface the original expat error,
        # whose line/column point at the actual fault rather than at our retry.
        raise strict_error from None

    logger.warning(
        "%s does not parse under its declared encoding (%s); recovered by decoding it as %s. "
        "This is a known GeniE export defect — the file is otherwise well-formed.",
        origin,
        strict_error,
        encoding,
    )
    return root


def read_genie_xml_root(xml_file: str | pathlib.Path) -> ET.Element:
    """Parse a GeniE concept XML from disk and return its root element.

    A strict parse is attempted first, so a file that is well-formed under its own
    declaration is handled by expat exactly as before, and a file that is genuinely
    malformed still raises instead of being quietly patched up. Only a ParseError
    sends us down the tolerant path.
    """
    path = pathlib.Path(xml_file)
    try:
        return ET.parse(str(path)).getroot()
    except ET.ParseError as strict_error:
        return _recover(path.read_bytes(), str(path), strict_error)


def genie_xml_root_from_bytes(data: bytes, origin: str = "<bytes>") -> ET.Element:
    """Same recovery as :func:`read_genie_xml_root`, for XML already held in memory.

    Used for the `modelData.xml` member of a `.gnx` workspace, which GeniE writes with
    the same declaration and the same disregard for it.
    """
    try:
        return ET.fromstring(data)
    except ET.ParseError as strict_error:
        return _recover(data, origin, strict_error)
