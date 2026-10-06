"""Standard ACIS Binary (SAB): recognising a body saved in binary.

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
"""

from __future__ import annotations

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


def binary_body_message(origin: str, member: str) -> str:
    """The refusal text: what was found, which GeniE option wrote it, and the way back to text."""
    return (
        f"{origin}: the ACIS body ({member}) is binary SAB ('{SAB_SIGNATURE.decode()}' header). "
        f"GeniE V9.3 writes it so when the workspace option '{GENIE_BINARY_OPTION}' "
        f"({GENIE_BINARY_JS}) is on. adapy reads text SAT bodies only: turn the option off in GeniE "
        f"(Edit > Rules > Compatibility, or GenieRules.Compatibility.enable(WriteACISBinaryFile, false)) "
        f"and save the workspace again, or import it into a workspace with the option off."
    )
