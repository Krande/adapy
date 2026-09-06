"""Write a Sesam GeniE workspace file (``.gnx``).

A ``.gnx`` is what GeniE saves a workspace as — the file the OS associates
with GeniE, so double-clicking one opens the model directly, where a plain
concept XML must be imported into a workspace by hand. Reverse-engineered
from workspaces GeniE V9.1 wrote; it is a zip of six members:

======================  ===========================================================
``modelData.xml``       the concept model — the same ``DNV_structure_concept_protocol``
                        document ``to_genie_xml`` writes, EXCEPT the ACIS body is
                        not embedded: every ``<flat_plate>`` keeps its
                        ``<sat_reference>`` face names and the body lives beside it
``acisGeometry.sat``    that ACIS body, as SAT text (an empty-body SAT when the
                        model has no sheet geometry)
``acisFaceFacets.bin``  GeniE's facet cache for the body; four zero bytes when
                        empty, and GeniE re-facets a body whose cache is empty
``modelData.js``        the journal — comment lines saying which GeniE started and
                        ended the session
``assemblyType.txt``    ``0`` (single assembly)
``lastUsedLicenseFileName.txt``  ``GENIE``
======================  ===========================================================

Two entry points: :func:`write_gnx` builds the workspace from a
:class:`~ada.Part` (the SAT body from the same face engine ``to_genie_xml``
uses, so the plate faces GeniE reads are the ones the concept XML names), and
:func:`gnx_from_genie_xml` repacks an existing concept XML — one carrying an
embedded SAT sequence, such as the streaming FEM-to-XML writer's — into a
workspace by lifting the body out into its own member.
"""

from __future__ import annotations

import datetime
import pathlib
import xml.etree.ElementTree as ET
import zipfile
from typing import TYPE_CHECKING, Callable

from ..sat_helpers import xml_elem_to_sat_text

if TYPE_CHECKING:
    from ada import Part

GNX_LICENSE_FILE = "GENIE"
GNX_ASSEMBLY_TYPE = "0"

#: What GeniE writes for a workspace with no sheet geometry: the ACIS header
#: and a single empty body. Byte-for-byte the shape of the real file's member,
#: down to the CRLF line ends.
_EMPTY_SAT = (
    "2000 0 1 0           \r\n"
    "18 SESAM - gmGeometry 14 ACIS 33.0.1 NT 24 {stamp} \r\n"
    "1000 9.9999999999999995e-07 1e-10 \r\n"
    "-0 body $-1 -1 -1 $-1 $-1 $-1 $-1 F #\r\n"
    "End-of-ACIS-data "
)


def _stamp(now: datetime.datetime | None = None) -> str:
    # ACIS header timestamp shape: "Mon Mar 02 13:54:43 2026".
    now = now or datetime.datetime.now()
    return now.strftime("%a %b %d %H:%M:%S %Y")


def _journal(now: datetime.datetime | None = None) -> str:
    now = now or datetime.datetime.now()
    when = now.strftime("%d-%b-%Y %H:%M:%S")
    from ada import __version__

    return f"// ada-py {__version__} wrote this workspace {when}\r\n"


def _strip_embedded_sat(root: ET.Element) -> str | None:
    """Remove ``<geometry>`` blocks carrying an embedded SAT from the tree and
    return the concatenated SAT text they held (None when there were none).

    Per-plate ``<geometry><sheet><sat_reference>`` blocks are NOT geometry
    carriers and stay: they are the face names the body is read by.
    """

    sat_parts: list[str] = []
    for parent in root.iter():
        for geom in list(parent.findall("geometry")):
            carriers = geom.findall("sat_embedded") + geom.findall("sat_embedded_sequence")
            if not carriers:
                continue
            for el in carriers:
                sat_parts.append(xml_elem_to_sat_text(el))
            parent.remove(geom)
    if not sat_parts:
        return None
    return "".join(sat_parts)


def _write_workspace_zip(gnx_path: pathlib.Path, xml_text: str, sat_text: str) -> None:
    gnx_path.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.datetime.now()
    with zipfile.ZipFile(gnx_path, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("lastUsedLicenseFileName.txt", GNX_LICENSE_FILE)
        z.writestr("modelData.js", _journal(now))
        z.writestr("acisGeometry.sat", sat_text)
        # An empty facet cache: GeniE re-facets the body on load.
        z.writestr("acisFaceFacets.bin", b"\x00\x00\x00\x00")
        z.writestr("assemblyType.txt", GNX_ASSEMBLY_TYPE)
        z.writestr("modelData.xml", xml_text)


def _finish_root(root: ET.Element, workspace_name: str) -> str:
    model = root.find("./model")
    if model is not None:
        # The workspace's model name is the workspace's own name — what the
        # title bar shows, and what GeniE would have set on save.
        model.set("name", workspace_name)
    body = ET.tostring(root, encoding="unicode")
    return '<?xml version="1.0" encoding="ASCII"?>\n' + body


def write_gnx(
    part: "Part",
    gnx_file: str | pathlib.Path,
    writer_postprocessor: Callable[[ET.Element, "Part"], None] | None = None,
) -> pathlib.Path:
    """Write ``part`` as a GeniE workspace.

    The concept model is the same one ``to_genie_xml(embed_sat=True)`` writes;
    the ACIS body goes into its own member instead of the XML. A model with no
    sheet geometry (beams only) gets GeniE's empty-body SAT.
    """

    from .write_xml import build_xml_tree

    gnx_path = pathlib.Path(gnx_file)
    tree, sw = build_xml_tree(part, embed_sat=True, writer_postprocessor=writer_postprocessor)
    root = tree.getroot()
    if sw is not None and not sw.is_empty:
        sat_text = sw.to_str().replace("\r\n", "\n").replace("\n", "\r\n")
    else:
        sat_text = _EMPTY_SAT.format(stamp=_stamp())
    xml_text = _finish_root(root, gnx_path.stem)
    _write_workspace_zip(gnx_path, xml_text, sat_text)
    return gnx_path


def gnx_from_genie_xml(xml_file: str | pathlib.Path, gnx_file: str | pathlib.Path | None = None) -> pathlib.Path:
    """Repack a concept XML on disk into a workspace.

    An embedded SAT sequence (the streaming FEM-to-XML writer's output, or a
    GeniE export with "embed geometry") is lifted into ``acisGeometry.sat``. A
    plain-polygon XML has no body to lift; it is written with the empty-body
    SAT and GeniE builds the ACIS from the polygons on load, exactly as it does
    when importing that XML by hand.
    """

    xml_path = pathlib.Path(xml_file)
    gnx_path = pathlib.Path(gnx_file) if gnx_file is not None else xml_path.with_suffix(".gnx")
    tree = ET.parse(str(xml_path))
    root = tree.getroot()
    sat_text = _strip_embedded_sat(root)
    if sat_text:
        sat_text = sat_text.replace("\r\n", "\n").replace("\n", "\r\n")
    else:
        sat_text = _EMPTY_SAT.format(stamp=_stamp())
    xml_text = _finish_root(root, gnx_path.stem)
    _write_workspace_zip(gnx_path, xml_text, sat_text)
    return gnx_path


def genie_xml_from_gnx(gnx_file: str | pathlib.Path, xml_file: str | pathlib.Path | None = None) -> pathlib.Path:
    """Unpack a workspace into a self-contained concept XML.

    The inverse of :func:`gnx_from_genie_xml`: ``modelData.xml`` with the body
    from ``acisGeometry.sat`` embedded back in as a ``sat_embedded_sequence``,
    which is the form the concept-XML reader (``ada.from_genie_xml``) expects.
    An empty body (no sheet geometry) is not embedded at all — the XML then
    reads exactly like a beams-only export.
    """

    from .write_sat_embedded import (
        embed_sat_geometry,
        sat_to_base64_segments,
        splice_cdata_segments,
    )

    gnx_path = pathlib.Path(gnx_file)
    xml_path = pathlib.Path(xml_file) if xml_file is not None else gnx_path.with_suffix(".xml")
    with zipfile.ZipFile(gnx_path) as z:
        names = set(z.namelist())
        if "modelData.xml" not in names:
            raise ValueError(f"{gnx_path.name}: not a Genie workspace (no modelData.xml member)")
        xml_bytes = z.read("modelData.xml")
        sat_text = z.read("acisGeometry.sat").decode("utf-8", errors="replace") if "acisGeometry.sat" in names else ""

    root = ET.fromstring(xml_bytes)
    # Whatever body the XML itself carries wins over the member (a workspace
    # Genie wrote never has both; a repacked one of ours never has the former).
    if _strip_embedded_sat(root) is None and _sat_has_faces(sat_text):
        structure_domain = root.find("./model/structure_domain")
        segments = sat_to_base64_segments(sat_text.replace("\r\n", "\n"))
        embed_sat_geometry(structure_domain, len(segments))
        xml_text = ET.tostring(root, encoding="unicode")
        xml_text = splice_cdata_segments(xml_text, segments)
    else:
        xml_text = ET.tostring(root, encoding="unicode")
    xml_path.parent.mkdir(parents=True, exist_ok=True)
    xml_path.write_text('<?xml version="1.0" encoding="ASCII"?>\n' + xml_text, encoding="utf-8")
    return xml_path


def _sat_has_faces(sat_text: str) -> bool:
    return any(line.split(" ")[1:2] == ["face"] for line in sat_text.splitlines() if line and line[0] == "-")


__all__ = ["write_gnx", "gnx_from_genie_xml", "genie_xml_from_gnx"]
