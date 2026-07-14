"""Embed an ACIS SAT body into a Genie XML as a zipped, base64 CDATA sequence.

Genie stores the SAT under ``structure_domain/geometry`` as a
``sat_embedded_sequence``: the body is zipped into a single ``b64temp.sat``
member, the *compressed* bytes are cut into fixed-size segments, and each
segment is base64-encoded on its own and wrapped in CDATA. Verified against a
Genie-authored export, whose four segments decode to 1 MiB, 1 MiB, 1 MiB and a
remainder.

The text is spliced in as raw CDATA after the tree is serialised, because
ElementTree has no CDATA node — assigning the base64 to ``.text`` would let it
escape the payload instead.
"""

from __future__ import annotations

import base64
import xml.etree.ElementTree as ET
import zipfile
from io import BytesIO

# Genie cuts the zipped body at exactly 1 MiB per segment.
SEGMENT_BYTES = 1024 * 1024

# The single member name inside the zip; the reader looks it up by this name
# (see ada.cadit.gxml.sat_helpers.xml_elem_to_sat_text).
ZIP_MEMBER = "b64temp.sat"

_PLACEHOLDER = "__ADA_CDATA_SEGMENT_{}__"


def sat_to_base64_segments(sat_body: str) -> list[str]:
    """Zip ``sat_body`` and return its base64 segments, in order.

    Each segment encodes its own slice of the compressed stream, so a reader
    concatenates the *decoded* bytes and unzips the result — it must not
    concatenate the base64 text (every segment is padded independently).
    """
    buf = BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zipf:
        zipf.writestr(ZIP_MEMBER, sat_body.encode("utf-8"))
    compressed = buf.getvalue()

    return [
        base64.b64encode(compressed[i : i + SEGMENT_BYTES]).decode() for i in range(0, len(compressed), SEGMENT_BYTES)
    ]


def embed_sat_geometry(root: ET.Element, n_segments: int) -> None:
    """Add ``<geometry><sat_embedded_sequence>`` with a placeholder per segment.

    Call last: Genie writes ``geometry`` as the final child of
    ``structure_domain``, after ``sets``.
    """
    geom = ET.SubElement(root, "geometry")
    seq = ET.SubElement(geom, "sat_embedded_sequence", dict(encoding="base64", compression="zip", tag_name="dnvscp"))
    for i in range(n_segments):
        ET.SubElement(seq, "cdata_segment").text = _PLACEHOLDER.format(i)


def splice_cdata_segments(xml_str: str, segments: list[str]) -> str:
    """Replace each placeholder with its real CDATA payload."""
    for i, segment in enumerate(segments):
        placeholder = _PLACEHOLDER.format(i)
        if placeholder not in xml_str:
            raise ValueError(f"CDATA placeholder {placeholder!r} missing from the serialised XML")
        xml_str = xml_str.replace(placeholder, f"<![CDATA[{segment}]]>")
    return xml_str
