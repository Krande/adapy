"""Streaming Genie-XML writer.

``write_xml`` (the default) builds the entire ``ElementTree`` DOM in memory and
flushes it once. For a FEM-derived model that DOM is a second full-size copy of
every plate/beam on top of the concept objects themselves — on a 57k-shell
jacket it adds ~380 MB of peak RSS.

This writer emits the same document but streams the per-object ``<structure>``
entries straight to the file: the bounded scaffold (properties, BCs, masses,
sets, loads, …) is built as a small DOM with a marker where the structures go,
then each beam/plate ``<structure>`` is serialised one at a time and written,
holding only one object's subtree at a time instead of the whole tree.

The per-object elements are produced by the *exact* same builders the DOM
writer uses (:func:`add_straight_beam`, :func:`add_plate_polygon`), so the
output is geometry-identical — only the assembly strategy differs.

Scope: the parametric (``embed_sat=False``) path only. The SAT-embedded path
shares one cross-referenced CDATA body and is inherently whole-model, so it
stays on :func:`write_xml`.
"""

from __future__ import annotations

import pathlib
import xml.etree.ElementTree as ET
from typing import Callable

from .write_bcs import add_concept_constraints, add_fem_boundary_conditions
from .write_equipments import add_equipments
from .write_hinges import add_hinges
from .write_load_case import add_loads
from .write_masses import add_masses
from .write_materials import add_materials
from .write_plates import add_plate_polygon, add_plate_polygon_data, thickness_name
from .write_sections import add_sections
from .write_sets import add_sets
from .write_xml import _XML_TEMPLATE

# A childless placeholder we serialise, then split the scaffold string on, so the
# streamed beam/plate structures land exactly where the DOM writer would emit
# them — before the BC / mass entries, matching add_beams/add_plates order.
_MARKER = "ADA__STREAMED_STRUCTURES__"


def write_xml_stream(
    part,
    xml_file,
    writer_postprocessor: Callable[[ET.Element, "object"], None] = None,
    merge_strategy=None,
) -> None:
    """Stream a Genie XML.

    ``merge_strategy`` (None | "none" | "coplanar" | ...) switches the *plate*
    source. ``None`` (default) streams the part's already-built ``Plate`` objects
    (the legacy concept path). Any strategy value sources plates from the
    object-free vectorized FEM-shell face engine
    (:func:`ada.fem.formats.mesh_faces.iter_faces`) instead — no Plate objects
    are ever materialised. Beams still come from the part's (bounded) beam set.
    """
    from ada import Beam, BeamTapered, Plate

    if not isinstance(xml_file, pathlib.Path):
        xml_file = pathlib.Path(xml_file)

    use_faces = merge_strategy is not None
    if use_faces:
        # plate materials live on the FEM shell sections; register them so
        # add_materials emits them and the streamed face material_refs resolve.
        _register_shell_materials(part)

    part.consolidate_sections()
    part.consolidate_materials()

    tree = ET.parse(_XML_TEMPLATE)
    root = tree.getroot()
    structure_domain = root.find("./model/structure_domain")
    structures_elem = ET.SubElement(structure_domain, "structures")
    properties = structure_domain.find("./properties")

    # ── bounded scaffold: properties ────────────────────────────────────────
    add_sections(properties, part)
    add_materials(properties, part)
    add_hinges(properties, part)

    # The thickness table lives in <properties>, ahead of the streamed plates,
    # so build it up front. Distinct thicknesses are few; this does not
    # materialise geometry (face path reads them straight off the FEM sections).
    thickness_map: dict[float, str] = {}
    thicknesses_elem = ET.SubElement(properties, "thicknesses")
    distinct_thicknesses = (
        _shell_thicknesses(part) if use_faces else [p.t for p in part.get_all_physical_objects(by_type=Plate)]
    )
    for t in distinct_thicknesses:
        if t not in thickness_map:
            name = thickness_name(t)
            thickness_map[t] = name
            tck = ET.Element("thickness", {"name": name, "default": "true"})
            tck.append(ET.Element("constant_thickness", {"th": str(t)}))
            thicknesses_elem.append(tck)

    # ── marker, then the remaining bounded structure-domain content ─────────
    ET.SubElement(structures_elem, _MARKER)
    add_fem_boundary_conditions(structures_elem, part)
    add_masses(structures_elem, part)
    add_sets(structure_domain, part)
    add_loads(root, part)
    add_concept_constraints(structures_elem, part)
    add_equipments(root, part)

    if writer_postprocessor:
        writer_postprocessor(root, part)

    scaffold = ET.tostring(root, encoding="unicode")
    # ElementTree serialises a childless element as ``<tag />`` (with the space).
    marker = f"<{_MARKER} />"
    if marker not in scaffold:  # be liberal about the serialisation variant
        marker = f"<{_MARKER}></{_MARKER}>"
    head, tail = scaffold.split(marker, 1)

    xml_file.parent.mkdir(exist_ok=True, parents=True)
    with open(xml_file, "w", encoding="utf-8") as fh:
        # Match the DOM writer byte-for-byte: tree.write(encoding="utf-8")
        # suppresses the XML declaration, so we emit none either.
        fh.write(head)
        _stream_structures(part, fh, thickness_map, Beam, BeamTapered, Plate, merge_strategy)
        fh.write(tail)


def _shell_thicknesses(part) -> list:
    """Distinct shell-section thicknesses across every FEM under ``part``."""
    out: list = []
    seen = set()
    for p in part.get_all_parts_in_assembly(include_self=True):
        fem = getattr(p, "fem", None)
        if fem is None:
            continue
        for sec in fem.sections.shells:
            t = getattr(sec, "thickness", None)
            if t is not None and t not in seen:
                seen.add(t)
                out.append(t)
    return out


def _register_shell_materials(part) -> None:
    """Add every FEM shell-section material to its part's material container so
    add_materials emits them and the streamed face ``material_ref`` resolves."""
    for p in part.get_all_parts_in_assembly(include_self=True):
        fem = getattr(p, "fem", None)
        if fem is None:
            continue
        for sec in fem.sections.shells:
            mat = getattr(sec, "material", None)
            if mat is None:
                continue
            if mat.name not in p.materials.name_map:
                mat.parent = p
                p.materials.add(mat)


def _stream_structures(part, fh, thickness_map, Beam, BeamTapered, Plate, merge_strategy) -> None:
    """Serialise one ``<structure>`` subtree at a time and write it out.

    Beams precede plates, matching the add_beams → add_plates order of the DOM
    writer. Each object's subtree is built on a throwaway parent, serialised,
    written, and dropped, so peak memory is one object's element tree.

    Plates come from the part's Plate objects (``merge_strategy is None``) or,
    for any strategy value, from the object-free vectorized face source.
    """
    import itertools

    from ada.api.spatial.eq_types import EquipRepr
    from ada.api.spatial.equipment import Equipment

    from .write_beams import add_straight_beam

    for beam in itertools.chain(
        part.get_all_physical_objects(by_type=Beam),
        part.get_all_physical_objects(by_type=BeamTapered),
    ):
        # mirror add_beams: equipment beams that aren't AS_IS are emitted by the
        # equipment writer, not as standalone structures.
        parent = beam.parent
        if isinstance(parent, Equipment) and parent.eq_repr != EquipRepr.AS_IS:
            continue
        tmp = ET.Element("structures")
        add_straight_beam(beam, tmp)
        for child in list(tmp):
            fh.write(ET.tostring(child, encoding="unicode"))

    if merge_strategy is None:
        for plate in part.get_all_physical_objects(by_type=Plate):
            tmp = ET.Element("structures")
            add_plate_polygon(plate, thickness_map[plate.t], tmp)
            for child in list(tmp):
                fh.write(ET.tostring(child, encoding="unicode"))
    else:
        from ada.fem.formats.mesh_faces import iter_faces

        for face in iter_faces(part, merge_strategy):
            tmp = ET.Element("structures")
            add_plate_polygon_data(
                face.name, face.outline, face.normal, thickness_map[face.thickness], face.material, tmp
            )
            for child in list(tmp):
                fh.write(ET.tostring(child, encoding="unicode"))
