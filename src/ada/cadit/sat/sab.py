"""Standard ACIS Binary (SAB): recognising a body saved in binary, and reading it as text.

A SAT body opens with its version line (``2000 0 1 0``); a SAB body opens with
the 15-byte signature ``ACIS BinaryFile`` followed by the same four header
integers as little-endian int32 and the same three header strings and three
doubles as tagged tokens. Every record after that is a sequence of one-byte
tags (0x04 int, 0x06 double, 0x07 string, 0x0A/0x0B logical, 0x0C pointer,
0x0D/0x0E entity type, 0x0F/0x10 subtype braces, 0x11 record end, 0x13/0x14
position/direction vectors, 0x15 enum), so nothing in it is readable as SAT
text. Measured on GeniE V9.3-00 workspaces saved with and without the
"Write ACIS files in binary format" option: same model, same record order,
one binary token per text token.

:func:`sat_text_of_body` is the one door a body goes through on its way to a
reader: text passes as it is, binary is rendered to the text GeniE would have
saved for it (``sab_codec``), and a binary body the codec cannot render
faithfully is refused by name rather than read in part.
"""

from __future__ import annotations

from ada.config import logger

SAB_SIGNATURE = b"ACIS BinaryFile"

#: The member names GeniE gives a workspace's body in the two formats. GeniE reads
#: whichever is present and decides the format by the name alone (measured: a
#: ``.sab`` member next to an XML whose option says text reads fine; text bytes
#: under the ``.sab`` name, or binary bytes under the ``.sat`` name, read as an
#: empty body with no error).
GNX_TEXT_BODY = "acisGeometry.sat"
GNX_BINARY_BODY = "acisGeometry.sab"

GENIE_BINARY_OPTION = "Write ACIS files in binary format"
GENIE_BINARY_JS = "GenieRules.Compatibility.enable(WriteACISBinaryFile, true)"


def is_sab(data: bytes) -> bool:
    """Does ``data`` begin with the SAB signature?"""
    return data[: len(SAB_SIGNATURE)] == SAB_SIGNATURE


def binary_body_message(origin: str, member: str, reason: str) -> str:
    """The refusal text: what was found, why it cannot be read, which GeniE option wrote it, and
    the way back to text."""
    return (
        f"{origin}: the ACIS body ({member}) is binary SAB ('{SAB_SIGNATURE.decode()}' header) and "
        f"holds what adapy's SAB reader has not been verified on: {reason}. "
        f"GeniE V9.3 writes a binary body when the workspace option '{GENIE_BINARY_OPTION}' "
        f"({GENIE_BINARY_JS}) is on. To read this model, turn the option off in GeniE "
        f"(Edit > Rules > Compatibility, or GenieRules.Compatibility.enable(WriteACISBinaryFile, false)) "
        f"and save the workspace again, or import it into a workspace with the option off."
    )


def sat_text_of_body(data: bytes, origin: str, member: str) -> str:
    """The body ``data`` (a workspace member or an embedded file named ``member``) as SAT text.

    The format is decided by the bytes, not the name. GeniE decides by the name, and reads a body
    whose bytes disagree with it as an empty model without a word (measured on V9.3-00); such a
    file did not come from GeniE, so it is read for what it holds and the disagreement is logged.

    Raises :class:`~ada.cadit.sat.exceptions.ACISBinaryBodyError` for a binary body outside what
    the codec was verified on (an unseen record type, subtype, flag or enum word, a transform, a
    periodic spline, another kernel version or a history section).
    """
    from ada.cadit.sat.exceptions import ACISBinaryBodyError
    from ada.cadit.sat.sab_codec import SabUnsupported, sat_text_from_sab

    binary_name = member.lower().endswith(".sab")
    if not is_sab(data):
        if binary_name:
            logger.warning(f"{origin}: {member} holds SAT text under a binary name; GeniE would read it as empty")
        return data.decode("utf-8", errors="replace")
    if not binary_name:
        logger.warning(f"{origin}: {member} holds binary SAB under a text name; GeniE would read it as empty")
    try:
        return sat_text_from_sab(data)
    except SabUnsupported as e:
        raise ACISBinaryBodyError(binary_body_message(origin, member, str(e))) from e
