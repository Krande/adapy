"""Memory-bounded ("streaming") IFC writer.

The default writer (:meth:`IfcStore.sync`) builds the whole ``ifcopenshell.file``
in memory and serialises it once at the end, so peak RSS grows ~linearly with
object count — a large FEM→IFC convert (e.g. a jacket whose shell mesh becomes
~100k plates) OOM-kills the worker.

ifcopenshell's file API cannot stream: ``remove()`` is ~1 ms/entity (O(N²) for a
chunked writer) and a fresh ``from_string`` file per chunk leaks on teardown. So
the dominant per-object entities — ``Plate`` solids, which a FEM shell mesh
explodes into — are hand-authored as STEP-physical-file (SPF) text and written
straight to disk. Everything else (spatial structure, sections, materials,
beams, shapes, …) is built once with the normal writer as a shared *preamble*;
only that preamble is ever resident, so peak memory is independent of the plate
count.

Entry point: :func:`stream_assembly_to_ifc`, used by
``Assembly.to_ifc(streaming=True)``.
"""

from __future__ import annotations

import os
import pathlib
import re
from typing import TYPE_CHECKING, Callable

from ada.config import logger

if TYPE_CHECKING:
    from ada.api.spatial.assembly import Assembly


# ── SPF text helpers ────────────────────────────────────────────────────────
def _f(x: float) -> str:
    """Format a float the way an IFC SPF reader expects (uppercase E exponent)."""
    s = repr(float(x))
    return s.replace("e", "E") if "e" in s else s


def _p(vec) -> str:
    return "(" + ",".join(_f(v) for v in vec) + ")"


_ID_LINE = re.compile(r"^#(\d+)=")


class _Emitter:
    """Hand-authors the SPF lines for one Plate, allocating ids from a counter."""

    def __init__(self, start_id: int, owner_id: int, body_ctx_id: int):
        self.nid = start_id
        self.owner_id = owner_id
        self.body_ctx_id = body_ctx_id

    def _curve(self, lines: list[str], curve) -> int:
        """Emit IfcCartesianPointList2D + IfcIndexedPolyCurve (with segment
        indices, incl. IfcArcIndex for fillets) — the form ada's reader expects.
        Returns the IfcIndexedPolyCurve id."""
        pts, seg_idx = curve.get_unique_points_and_segment_indices()
        pts = pts.tolist() if hasattr(pts, "tolist") else pts
        ptlist = self.nid
        coords = ",".join("(" + ",".join(_f(c) for c in p) + ")" for p in pts)
        lines.append(f"#{ptlist}=IfcCartesianPointList2D(({coords}),$);")
        segs = ",".join(
            ("IfcArcIndex" if len(s) == 3 else "IfcLineIndex") + "((" + ",".join(str(int(x)) for x in s) + "))"
            for s in seg_idx
        )
        cid = ptlist + 1
        lines.append(f"#{cid}=IfcIndexedPolyCurve(#{ptlist},({segs}),$);")
        self.nid = cid + 1
        return cid

    def plate(self, pl, lines: list[str]) -> int:
        """Append the SPF lines for ``pl``; return its IfcPlate id."""
        g = pl.solid_geom().geometry  # ExtrudedAreaSolid
        op = pl.placement.to_axis2placement3d()
        pos = g.position
        sa = g.swept_area

        a = self.nid
        lines.append(f"#{a}=IfcCartesianPoint({_p(op.location)});")
        lines.append(f"#{a + 1}=IfcDirection({_p(op.axis)});")
        lines.append(f"#{a + 2}=IfcDirection({_p(op.ref_direction)});")
        lines.append(f"#{a + 3}=IfcAxis2Placement3D(#{a},#{a + 1},#{a + 2});")
        lines.append(f"#{a + 4}=IfcLocalPlacement($,#{a + 3});")
        lines.append(f"#{a + 5}=IfcCartesianPoint({_p(pos.location)});")
        lines.append(f"#{a + 6}=IfcDirection({_p(pos.axis)});")
        lines.append(f"#{a + 7}=IfcDirection({_p(pos.ref_direction)});")
        lines.append(f"#{a + 8}=IfcAxis2Placement3D(#{a + 5},#{a + 6},#{a + 7});")
        self.nid = a + 9

        outer = self._curve(lines, sa.outer_curve)
        inners = [self._curve(lines, c) for c in (sa.inner_curves or [])]
        prof = self.nid
        if inners:
            voids = "(" + ",".join(f"#{i}" for i in inners) + ")"
            lines.append(f"#{prof}=IfcArbitraryProfileDefWithVoids(.AREA.,$,#{outer},{voids});")
        else:
            lines.append(f"#{prof}=IfcArbitraryClosedProfileDef(.AREA.,$,#{outer});")
        edir = prof + 1
        lines.append(f"#{edir}=IfcDirection({_p(g.extruded_direction)});")
        solid = edir + 1
        lines.append(f"#{solid}=IfcExtrudedAreaSolid(#{prof},#{a + 8},#{edir},{_f(g.depth)});")
        body = solid + 1
        lines.append(f"#{body}=IfcShapeRepresentation(#{self.body_ctx_id},'Body','SolidModel',(#{solid}));")
        pds = body + 1
        lines.append(f"#{pds}=IfcProductDefinitionShape($,$,(#{body}));")
        pid = pds + 1
        nm = _spf_str(pl.name)
        lines.append(
            f"#{pid}=IfcPlate('{pl.guid}',#{self.owner_id},{nm},{nm},$,#{a + 4},#{pds},$,$);"
        )
        self.nid = pid + 1
        return pid


def _spf_str(s) -> str:
    """Quote a Python string as an SPF string literal (apostrophes doubled)."""
    if s is None:
        return "$"
    return "'" + str(s).replace("'", "''") + "'"


# ── main entry point ────────────────────────────────────────────────────────
def stream_assembly_to_ifc(
    assembly: "Assembly",
    destination: str | os.PathLike,
    include_fem: bool = False,
    progress_callback: Callable[[int, int], None] | None = None,
) -> None:
    """Write ``assembly`` to ``destination`` with bounded peak memory.

    Plain ``Plate`` objects are streamed as hand-authored SPF text; every other
    physical object plus the spatial/section/material structure is built once via
    the normal writer and kept resident as the preamble.
    """
    from ada import Beam, Pipe, Plate
    from ada.cadit.ifc.utils import create_guid, write_elem_property_sets
    from ada.cadit.ifc.write.write_ifc import IfcWriter

    destination = pathlib.Path(destination).resolve().absolute()
    os.makedirs(destination.parent, exist_ok=True)

    store = assembly.ifc_store
    f = store.f
    writer = IfcWriter(store)
    store.writer = writer

    # ── preamble: spatial structure, sections, materials ──
    assembly.consolidate_sections()
    assembly.consolidate_materials()
    store.update_owner(assembly.user)
    store.reset_query_caches()
    writer.sync_spatial_hierarchy(include_fem=include_fem)
    writer.sync_sections()
    writer.sync_materials()
    owner_id = store.owner_history.id()
    body_ctx_id = store.get_context("Body").id()

    # Partition: plain Plates stream as text; everything else is built normally.
    streamable: list = []
    others: list = []
    for obj in assembly.get_all_physical_objects():
        (streamable if type(obj) is Plate else others).append(obj)

    spatial_id: dict[str, int] = {}
    for p in assembly.get_all_parts_in_assembly(include_self=True):
        try:
            spatial_id[p.guid] = f.by_guid(p.guid).id()
        except RuntimeError:
            pass
    mat_ent_id: dict[str, int] = {}
    stub_rel_ids: set[int] = set()
    for rel in f.by_type("IfcRelAssociatesMaterial"):
        mat_ent_id[rel.GlobalId] = rel.RelatingMaterial.id()
        stub_rel_ids.add(rel.id())

    spatial_members: dict[str, list[int]] = {}
    material_members: dict[str, list[int]] = {}

    def _record_spatial(guid, ifc_id):
        spatial_members.setdefault(guid, []).append(ifc_id)

    def _record_material(guid, ifc_id):
        material_members.setdefault(guid, []).append(ifc_id)

    # ── build the non-streamed objects (kept resident in the preamble) ──
    for obj in others:
        el = writer.add(obj)
        if el is None:
            continue
        writer.create_ifc_openings(obj, el)
        write_elem_property_sets(obj.metadata, el, f, store.owner_history)
        if isinstance(obj, Pipe):
            continue  # a Pipe contains its own segments in the spatial structure
        _record_spatial(obj.parent.guid, el.id())
        # Beams attach their own IfcRelAssociatesMaterial inside write_ifc_beam;
        # other objects get a material rel hand-emitted in the trailing pass.
        mat = getattr(obj, "material", None)
        if mat is not None and not isinstance(obj, Beam):
            _record_material(mat.guid, el.id())

    # Beam→IfcBeamType memberships are queued by write_ifc_beam (deferred to
    # avoid O(N²) growth); flush them now — they reference resident beams.
    store.flush_rel_defines_by_type()

    # Features that reference objects by GlobalId can't be reproduced for the
    # streamed plates (those entities aren't in the in-memory file). They're
    # absent in the FEM→IFC path this writer targets; warn rather than drop
    # silently so an operator can disable streaming for full fidelity.
    dropped = []
    if assembly.presentation_layers.layers:
        dropped.append("presentation layers")
    if any(getattr(p, "groups", None) for p in assembly.get_all_subparts(include_self=True)):
        dropped.append("groups")
    if next(iter(assembly.get_all_welds()), None) is not None:
        dropped.append("welds")
    if dropped:
        logger.warning(
            "streaming IFC writer omits %s for streamed plates; disable to_ifc(streaming) for full fidelity",
            ", ".join(dropped),
        )

    # ── serialise the preamble, dropping empty stub material-rels + the footer ──
    pre_text = f.wrapped_data.to_string()
    cut = pre_text.rindex("ENDSEC;")
    head = [
        ln
        for ln in pre_text[:cut].split("\n")
        if not (_ID_LINE.match(ln) and int(_ID_LINE.match(ln).group(1)) in stub_rel_ids)
    ]
    out = open(destination, "w")
    try:
        out.write("\n".join(head).rstrip("\n") + "\n")

        # ── stream the plates as text ──
        emitter = _Emitter(max((e.id() for e in f), default=0) + 1, owner_id, body_ctx_id)
        total = len(streamable)
        skipped = 0
        lines: list[str] = []
        for i, pl in enumerate(streamable, 1):
            try:
                pid = emitter.plate(pl, lines)
            except Exception as exc:  # noqa: BLE001 — a bad plate shouldn't sink the file
                skipped += 1
                if skipped <= 5:
                    logger.warning(f"streaming IFC: skipped plate {pl.name!r}: {exc}")
                # don't clear `lines` — it holds prior plates not yet flushed
                continue
            _record_spatial(pl.parent.guid, pid)
            mat = getattr(pl, "material", None)
            if mat is not None:
                _record_material(mat.guid, pid)
            if i % 2000 == 0 or i == total:
                out.write("\n".join(lines) + "\n")
                lines.clear()
                if progress_callback is not None:
                    progress_callback(i, total)
        if lines:
            out.write("\n".join(lines) + "\n")

        # ── trailing relationships (hand-emitted from recorded ids) ──
        nid = emitter.nid

        def _refs(ids):
            return "(" + ",".join(f"#{i}" for i in ids) + ")"

        for guid, members in spatial_members.items():
            if members and guid in spatial_id:
                out.write(
                    f"#{nid}=IfcRelContainedInSpatialStructure('{create_guid()}',#{owner_id},"
                    f"'Physical model',$,{_refs(members)},#{spatial_id[guid]});\n"
                )
                nid += 1
        for guid, members in material_members.items():
            if members and guid in mat_ent_id:
                out.write(
                    f"#{nid}=IfcRelAssociatesMaterial('{create_guid()}',#{owner_id},$,$,"
                    f"{_refs(members)},#{mat_ent_id[guid]});\n"
                )
                nid += 1

        out.write("ENDSEC;\nEND-ISO-10303-21;\n")
    finally:
        out.close()

    if skipped:
        logger.warning(f"streaming IFC: {skipped}/{total} plates skipped (geometry not emittable)")
    logger.info(f"streaming IFC write complete: {total - skipped} plates + {len(others)} other objects")
