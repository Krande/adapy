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
writer uses (:func:`add_straight_beam`, :func:`add_plate_polygon` /
:func:`add_plate_sat`), so the output is byte-identical — only the assembly
strategy differs.

``embed_sat`` composes with this: the ACIS body is one cross-referenced,
imprinted whole-model blob and is necessarily built in memory in one piece, but
the concept ``<structure>`` entries — the part that scales with model size —
still stream.
"""

from __future__ import annotations

import pathlib
import xml.etree.ElementTree as ET
from typing import Callable

from ...sat.write.writer import part_to_sat_writer
from .write_bcs import add_concept_constraints, add_fem_boundary_conditions
from .write_equipments import add_equipments
from .write_hinges import add_hinges
from .write_load_case import add_loads
from .write_masses import add_masses
from .write_materials import add_materials
from .write_plates import (
    add_curved_shell_sat_data,
    add_plate_curved_polygon,
    add_plate_polygon,
    add_plate_polygon_data,
    add_plate_sat,
    thickness_name,
)
from .write_sat_embedded import (
    embed_sat_geometry,
    sat_to_base64_segments,
    splice_cdata_segments,
)
from .write_sections import add_sections
from .write_sets import add_sets
from .write_xml import _XML_TEMPLATE

# A childless placeholder we serialise, then split the scaffold string on, so the
# streamed beam/plate structures land exactly where the DOM writer would emit
# them — before the BC / mass entries, matching add_beams/add_plates order.
_MARKER = "ADA__STREAMED_STRUCTURES__"


def _analytic_merge_strategy(merge_strategy):
    """The :class:`MergeStrategy` for an analytic request, or ``None``.

    ``surface``/``panel`` are the analytic strategies; ``cylinder``/``analytic``
    are the aliases the STEP/IFC FEM writers accept (both mean ``surface``). Any
    other value (``coplanar``, ``none``, ``planar``) is not analytic — those
    stream flat polygons — so this returns ``None`` for them.
    """
    from ada.fem.formats.mesh_faces import MergeStrategy

    if merge_strategy is None or isinstance(merge_strategy, bool):
        return None
    s = str(merge_strategy).lower()
    if s in ("surface", "cylinder", "analytic"):
        return MergeStrategy.SURFACE
    if s == "panel":
        return MergeStrategy.PANEL
    return None


def write_xml_stream(
    part,
    xml_file,
    writer_postprocessor: Callable[[ET.Element, "object"], None] = None,
    merge_strategy=None,
    embed_sat: bool = False,
) -> None:
    """Stream a Genie XML.

    ``merge_strategy`` (None | "none" | "coplanar" | ...) switches the *plate*
    source. ``None`` (default) streams the part's already-built ``Plate`` objects
    (the legacy concept path). Any strategy value sources plates from the
    object-free vectorized FEM-shell face engine
    (:func:`ada.fem.formats.mesh_faces.iter_faces`) instead — no Plate objects
    are ever materialised. Beams still come from the part's (bounded) beam set.

    ``embed_sat`` writes the plates as references into an embedded ACIS body
    instead of as polygons. The body is shared and imprinted across the whole
    model, so it is necessarily built in memory in one piece — only the concept
    ``<structure>`` entries stream. It is incompatible with ``merge_strategy``
    (no Plate objects to build the body from); the caller checks that.
    """
    from ada import Beam, BeamTapered, Plate

    if not isinstance(xml_file, pathlib.Path):
        xml_file = pathlib.Path(xml_file)

    # The analytic strategies (surface/panel/cylinder/analytic) author their
    # recognised cylinder/panel patches into the embedded SAT body and reference
    # them as <curved_shell> — the only Genie-XML form that carries a curved
    # surface. This is the one case where embed_sat composes with a face source.
    analytic_strategy = _analytic_merge_strategy(merge_strategy) if embed_sat else None
    analytic_mode = analytic_strategy is not None

    use_faces = merge_strategy is not None
    if embed_sat and use_faces and not analytic_mode:
        raise ValueError("write_xml_stream: embed_sat is incompatible with merge_strategy")
    if use_faces:
        # plate materials live on the FEM shell sections; register them so
        # add_materials emits them and the streamed face material_refs resolve.
        _register_shell_materials(part)
    else:
        # merge_strategy is None but a part may still carry only a FEM mesh (no
        # materialised Beam/Plate) — the streamer then fuses concepts straight from
        # the mesh (mirroring the IFC/STEP streaming writers), so register the
        # sections + materials those fused objects reference here, before the
        # scaffolding + consolidation, so add_sections/add_materials emit them.
        _register_fem_fused_scaffolding(part)

    part.consolidate_sections()
    part.consolidate_materials()

    # embed_sat builds one shared ACIS body from the part's Plate objects — but a
    # part that fuses its plates from the FEM mesh has none to build it from (and
    # materialising them all would defeat the streaming). Fall back to polygon plates
    # in that case rather than emitting <sheet>s that reference absent SAT faces.
    # (The analytic path builds its SAT straight from the mesh faces, so it is
    # exempt from this.)
    if (
        embed_sat
        and not analytic_mode
        and any(_fuses_from_fem(p) for p in part.get_all_parts_in_assembly(include_self=True))
    ):
        from ada.config import logger as _logger

        _logger.info("write_xml_stream: a part streams plates from its FEM mesh; embed_sat disabled (polygon plates)")
        embed_sat = False

    # The ACIS body must exist before the plates stream: each <sheet> references
    # its faces by the name the SAT writer minted for them.
    analytic_records = None
    if analytic_mode:
        from ada.cadit.sat.write import sat_entities as se

        from .write_analytic_faces import analytic_faces_to_sat_writer

        sw, analytic_records = analytic_faces_to_sat_writer(part, analytic_strategy)
        # No curved faces authored (an all-flat model): drop the empty body so the
        # records stream as plain flat_plate polygons with no dangling <sheet> refs.
        if not sw.get_entities_by_type(se.Face):
            sw = None
    else:
        sw = part_to_sat_writer(part) if embed_sat else None

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
    from ada.api.plates import PlateCurved

    if use_faces:
        distinct_thicknesses = _shell_thicknesses(part)
    else:
        # Materialised plates, plus the FEM shell thicknesses of any part that fuses
        # its plates straight from the mesh (no-op unless a part fuses).
        distinct_thicknesses = [p.t for p in part.get_all_physical_objects(by_type=(Plate, PlateCurved))]
        distinct_thicknesses += _shell_thicknesses(part)
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

    # <geometry> goes last, after <sets> — matching Genie's own export. A model
    # with no plates has no ACIS body, so there is nothing to embed (and no
    # plate will ask sw for a face name either).
    segments = [] if sw is None or sw.is_empty else sat_to_base64_segments(sw.to_str())
    if segments:
        embed_sat_geometry(structure_domain, len(segments))

    scaffold = ET.tostring(root, encoding="unicode")
    if segments:
        scaffold = splice_cdata_segments(scaffold, segments)
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
        _stream_structures(
            part, fh, thickness_map, Beam, BeamTapered, Plate, merge_strategy, sw, analytic_records=analytic_records
        )
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


def _fuses_from_fem(p) -> bool:
    """True for a part whose Beam/Plate haven't been materialised but whose FEM mesh
    carries elements — the streamer fuses its concepts straight from the mesh (same
    predicate the IFC/STEP streaming writers use)."""
    from ada.cadit.step.write.ap242_stream import _part_fuses_from_fem

    return _part_fuses_from_fem(p)


def _register_fem_fused_scaffolding(part) -> None:
    """Register the sections + materials referenced by the Beam/Plate concepts a part
    fuses from its FEM mesh, so add_sections/add_materials emit them and the streamed
    section_ref/material_ref resolve. Sourced from the FEM sections (a handful of
    distinct entries), so nothing geometry-bearing is materialised. No-op for a
    part that already carries built concepts."""
    for p in part.get_all_parts_in_assembly(include_self=True):
        if not _fuses_from_fem(p):
            continue
        for fem_sec in p.fem.sections.lines:
            sec = getattr(fem_sec, "section", None)
            mat = getattr(fem_sec, "material", None)
            if sec is not None and sec.name not in p.sections.name_map:
                sec.parent = p
                p.sections.add(sec)
            if mat is not None and mat.name not in p.materials.name_map:
                mat.parent = p
                p.materials.add(mat)
    _register_shell_materials(part)


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


def _stream_structures(
    part, fh, thickness_map, Beam, BeamTapered, Plate, merge_strategy, sw=None, analytic_records=None
) -> None:
    """Serialise one ``<structure>`` subtree at a time and write it out.

    Beams precede plates, matching the add_beams → add_plates order of the DOM
    writer. Each object's subtree is built on a throwaway parent, serialised,
    written, and dropped, so peak memory is one object's element tree.

    Plates come from the part's Plate objects (``merge_strategy is None``) or,
    for any strategy value, from the object-free vectorized face source. With
    ``sw`` they are written as SAT face references instead of polygons.
    """
    import itertools

    from ada.api.beams import BeamRevolve, BeamSweep
    from ada.api.spatial.eq_types import EquipRepr
    from ada.api.spatial.equipment import Equipment
    from ada.config import logger as _logger

    from .write_beams import add_straight_beam

    for beam in itertools.chain(
        part.get_all_physical_objects(by_type=Beam),
        part.get_all_physical_objects(by_type=BeamTapered),
        # Curved-axis beams: Genie XML's curved_beam element reads back as
        # chord segments anyway (see read_beams.seg_to_beam), so emit the
        # straight chord rather than silently dropping the member.
        part.get_all_physical_objects(by_type=BeamRevolve),
        part.get_all_physical_objects(by_type=BeamSweep),
    ):
        # mirror add_beams: equipment beams that aren't AS_IS are emitted by the
        # equipment writer, not as standalone structures.
        parent = beam.parent
        if isinstance(parent, Equipment) and parent.eq_repr != EquipRepr.AS_IS:
            continue
        if isinstance(beam, (BeamRevolve, BeamSweep)):
            _logger.warning(
                f"gxml-write: {type(beam).__name__} {beam.name!r} written as a straight chord beam "
                "(curved axis not supported by the Genie XML writer)"
            )
        tmp = ET.Element("structures")
        add_straight_beam(beam, tmp, sw)
        for child in list(tmp):
            fh.write(ET.tostring(child, encoding="unicode"))

    # Beams fused straight from the FEM mesh (parts carrying only a mesh, no built
    # concepts) — one transient Beam at a time, bounded memory, mirroring the
    # IFC/STEP streaming writers. Emitted after the materialised beams so all beams
    # still precede all plates.
    for p in part.get_all_parts_in_assembly(include_self=True):
        if not _fuses_from_fem(p):
            continue
        for beam in p.iter_objects_from_fem(beams=True, plates=False):
            tmp = ET.Element("structures")
            add_straight_beam(beam, tmp, sw)
            for child in list(tmp):
                fh.write(ET.tostring(child, encoding="unicode"))

    if analytic_records is not None:
        # Analytic FEM path: each record is a curved shell (SAT face refs, already
        # authored into `sw`) or a flat plate (boundary polygon). This is what
        # lets a tube's shell mesh arrive as a handful of <curved_shell>s instead
        # of thousands of coplanar <flat_plate> facets.
        for rec in analytic_records:
            tmp = ET.Element("structures")
            if rec.face_refs:
                add_curved_shell_sat_data(
                    rec.name, thickness_map[rec.thickness], rec.material, tmp, rec.face_refs
                )
            else:
                add_plate_polygon_data(rec.name, rec.outline, rec.normal, thickness_map[rec.thickness], rec.material, tmp)
            for child in list(tmp):
                fh.write(ET.tostring(child, encoding="unicode"))
        return

    if merge_strategy is None:
        from ada.api.plates import PlateCurved
        from ada.config import logger

        for plate in part.get_all_physical_objects(by_type=Plate):
            tmp = ET.Element("structures")
            if sw is not None:
                add_plate_sat(plate, thickness_map[plate.t], tmp, sw)
            else:
                add_plate_polygon(plate, thickness_map[plate.t], tmp)
            for child in list(tmp):
                fh.write(ET.tostring(child, encoding="unicode"))
        for plate in part.get_all_physical_objects(by_type=PlateCurved):
            # No native curved-plate element in Genie XML without SAT-embedded
            # spline geometry — degrade to the boundary polygon rather than
            # silently dropping the plate (see add_plate_curved_polygon).
            tmp = ET.Element("structures")
            if not add_plate_curved_polygon(plate, thickness_map[plate.t], tmp):
                logger.warning(f"gxml-write: PlateCurved {plate.name!r} has no usable boundary; dropped")
                continue
            for child in list(tmp):
                fh.write(ET.tostring(child, encoding="unicode"))
        # Plates fused straight from the FEM mesh (1:1 element→plate, no merge) for
        # parts that carry only a mesh — bounded, one transient Plate at a time.
        for p in part.get_all_parts_in_assembly(include_self=True):
            if not _fuses_from_fem(p):
                continue
            for plate in p.iter_objects_from_fem(beams=False, plates=True, merge_strategy=None):
                tmp = ET.Element("structures")
                if sw is not None:
                    add_plate_sat(plate, thickness_map[plate.t], tmp, sw)
                else:
                    add_plate_polygon(plate, thickness_map[plate.t], tmp)
                for child in list(tmp):
                    fh.write(ET.tostring(child, encoding="unicode"))
    else:
        from ada.fem.formats.mesh_faces import iter_faces

        # Genie XML has no curved-surface concept — keep the analytic strategies
        # polygon-only so cylinder/panel patches merge as their coplanar flats
        # instead of arriving as unrepresentable geom faces.
        for face in iter_faces(part, merge_strategy, allow_analytic=False):
            tmp = ET.Element("structures")
            add_plate_polygon_data(
                face.name, face.outline, face.normal, thickness_map[face.thickness], face.material, tmp
            )
            for child in list(tmp):
                fh.write(ET.tostring(child, encoding="unicode"))
