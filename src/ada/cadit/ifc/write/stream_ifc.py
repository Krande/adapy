"""Memory-bounded ("streaming") IFC writer.

The default writer (:meth:`IfcStore.sync`) builds the whole ``ifcopenshell.file``
in memory and serialises it once at the end, so peak RSS grows ~linearly with
object count — a large FEM→IFC convert (a jacket shell mesh becomes ~100k
plates) OOM-kills the worker.

ifcopenshell's file API cannot stream: ``remove()`` is ~1 ms/entity (O(N²) for a
chunked writer) and a fresh ``from_string`` file per chunk leaks on teardown. So
the dominant per-object entities — ``Plate`` solids — are hand-authored as
STEP-physical-file (SPF) text and written straight to disk. The spatial
structure, sections, materials and all non-plate objects (beams, shapes, …) are
built once with the normal writer as a shared *preamble*; only that preamble is
ever resident.

Two plate sources are supported, both bounded:
  * **fused** (FEM→IFC): the part's shell elements are turned into plates one at
    a time during the write — the ~100k-plate concept set is never materialised.
    The converter leaves plates unbuilt (``create_objects_from_fem(skip_plates``
    ``=True)``) so this path is taken.
  * **pre-built**: a non-FEM model whose ``part.plates`` already exist (e.g. a
    parametric CAD model) streams those objects as text.

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
        # (r,g,b,transparency) → (colour_id, shading_id, style_id). The colour +
        # surface-style entities are SHARED across every plate of that colour
        # (one set, referenced by many IfcStyledItems) and emitted once in the
        # trailing pass; only the per-plate IfcStyledItem is unavoidably 1:1.
        self.color_styles: dict = {}

    def _surface_style(self, key: tuple) -> int:
        """Return the shared IfcSurfaceStyle id for ``key``, reserving the 3
        shared style-entity ids on first sight (forward-referenced; emitted in
        the trailing pass)."""
        s = self.color_styles.get(key)
        if s is None:
            s = (self.nid, self.nid + 1, self.nid + 2)
            self.nid += 3
            self.color_styles[key] = s
        return s[2]

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
        lines.append(f"#{pid}=IfcPlate('{pl.guid}',#{self.owner_id},{nm},{nm},$,#{a + 4},#{pds},$,$);")
        self.nid = pid + 1

        col = getattr(pl, "color", None)
        if col is not None:
            rgb = col.rgb
            key = (float(rgb[0]), float(rgb[1]), float(rgb[2]), float(getattr(col, "transparency", 0.0) or 0.0))
            style_id = self._surface_style(key)
            sitem = self.nid
            lines.append(f"#{sitem}=IfcStyledItem(#{solid},(#{style_id}),$);")
            self.nid = sitem + 1
        return pid


def _spf_str(s) -> str:
    """Quote a Python string as an SPF string literal (apostrophes doubled)."""
    if s is None:
        return "$"
    return "'" + str(s).replace("'", "''") + "'"


def _register_plate_materials(part, shells, GeomRepr) -> dict:
    """First pass over a fused part's shell elements: register their materials
    on the part (no geometry built) so they land in the preamble. Returns the
    name→material cache reused by the per-element plate build."""
    mat_dict: dict = {}
    for elem in shells:
        fs = elem.fem_sec
        if fs is None or fs.material is None:
            continue
        if fs.type == GeomRepr.SOLID or getattr(fs, "thickness", None) is None:
            continue
        name = fs.material.name
        if name not in mat_dict:
            mat_dict[name] = part.materials.add(fs.material.copy_to(name, parent=part))
    return mat_dict


# ── main entry point ────────────────────────────────────────────────────────
def stream_assembly_to_ifc(
    assembly: "Assembly",
    destination: str | os.PathLike,
    include_fem: bool = False,
    progress_callback: Callable[[int, int], None] | None = None,
) -> None:
    """Write ``assembly`` to ``destination`` with bounded peak memory."""
    from ada import Beam, Pipe, Plate
    from ada.base.types import GeomRepr
    from ada.cadit.ifc.utils import create_guid, write_elem_property_sets
    from ada.cadit.ifc.write.write_ifc import IfcWriter
    from ada.fem.formats.utils import convert_part_elem_bm_to_beams

    destination = pathlib.Path(destination).resolve().absolute()
    os.makedirs(destination.parent, exist_ok=True)

    store = assembly.ifc_store
    f = store.f
    writer = IfcWriter(store)
    store.writer = writer

    # ── Decide each part's plate source. A FEM part whose plates haven't been
    # materialised is *fused*: build its beams + register its plate materials
    # now (so both land in the preamble), then stream its shells one at a time
    # via Part.iter_objects_from_fem during the write. ──
    fused: list = []  # (part, n_shells) — plates streamed from the FEM mesh
    for part in assembly.get_all_parts_in_assembly(include_self=True):
        fem = getattr(part, "fem", None)
        if fem is None or len(part.plates):
            continue
        shells = list(fem.elements.shell)
        if not shells:
            continue
        if not len(part.beams) and len(list(fem.elements.lines)):
            part._beams = convert_part_elem_bm_to_beams(part)
        _register_plate_materials(part, shells, GeomRepr)
        fused.append((part, len(shells)))

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

    # Pre-built plain Plates stream as text; everything else (beams, shapes, …)
    # is built once via the normal writer.
    prebuilt_plates, others = [], []
    for obj in assembly.get_all_physical_objects():
        (prebuilt_plates if type(obj) is Plate else others).append(obj)

    spatial_id = {}
    for part in assembly.get_all_parts_in_assembly(include_self=True):
        try:
            spatial_id[part.guid] = f.by_guid(part.guid).id()
        except RuntimeError:
            pass
    mat_ent_id, stub_rel_ids = {}, set()
    for rel in f.by_type("IfcRelAssociatesMaterial"):
        mat_ent_id[rel.GlobalId] = rel.RelatingMaterial.id()
        stub_rel_ids.add(rel.id())
    # Pin the post-consolidation materials by name so fused plates reference the
    # same objects the preamble wrote (their guids are in mat_ent_id); without
    # this a post-consolidation materials.add() would mint a fresh-guid copy.
    mat_cache = {m.name: m for m in assembly.get_all_materials()}

    spatial_members: dict = {}
    material_members: dict = {}

    def _record_spatial(guid, ifc_id):
        spatial_members.setdefault(guid, []).append(ifc_id)

    def _record_material(guid, ifc_id):
        material_members.setdefault(guid, []).append(ifc_id)

    # ── build the non-plate objects (kept resident in the preamble) ──
    for obj in others:
        el = writer.add(obj)
        if el is None:
            continue
        writer.create_ifc_openings(obj, el)
        write_elem_property_sets(obj.metadata, el, f, store.owner_history)
        if isinstance(obj, Pipe):
            continue  # a Pipe contains its own segments in the spatial structure
        _record_spatial(obj.parent.guid, el.id())
        mat = getattr(obj, "material", None)
        if mat is not None and not isinstance(obj, Beam):
            _record_material(mat.guid, el.id())
    store.flush_rel_defines_by_type()  # beam→IfcBeamType rels (reference resident beams)

    # Features that reference objects by GlobalId can't be reproduced for the
    # streamed plates; warn rather than drop silently (absent in FEM→IFC).
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

    # ── serialise the preamble (drop empty stub material-rels + the footer) ──
    pre_text = f.wrapped_data.to_string()
    cut = pre_text.rindex("ENDSEC;")
    head = [
        ln
        for ln in pre_text[:cut].split("\n")
        if not (_ID_LINE.match(ln) and int(_ID_LINE.match(ln).group(1)) in stub_rel_ids)
    ]
    out = open(destination, "w")
    skipped = 0
    total = 0
    try:
        out.write("\n".join(head).rstrip("\n") + "\n")
        emitter = _Emitter(max((e.id() for e in f), default=0) + 1, owner_id, body_ctx_id)
        lines: list[str] = []

        def _emit(pl) -> None:
            nonlocal skipped, total
            try:
                pid = emitter.plate(pl, lines)
            except Exception as exc:  # noqa: BLE001 — a bad plate shouldn't sink the file
                skipped += 1
                if skipped <= 5:
                    logger.warning(f"streaming IFC: skipped plate {getattr(pl, 'name', '?')!r}: {exc}")
                return
            total += 1
            _record_spatial(pl.parent.guid, pid)
            mat = getattr(pl, "material", None)
            if mat is not None:
                _record_material(mat.guid, pid)

        # 1) pre-built plates
        for i, pl in enumerate(prebuilt_plates, 1):
            _emit(pl)
            if i % 2000 == 0 or i == len(prebuilt_plates):
                out.write("\n".join(lines) + "\n")
                lines.clear()
                if progress_callback is not None:
                    progress_callback(i, len(prebuilt_plates))
        if lines:
            out.write("\n".join(lines) + "\n")
            lines.clear()

        # 2) fused plates — Part.iter_objects_from_fem builds one element's
        # plate(s) at a time; being ``detached`` they carry no material back-ref
        # and free as soon as _emit drops them, so peak memory stays bounded.
        for part, n_shells in fused:
            cnt = 0
            for pl in part.iter_objects_from_fem(beams=False, plates=True, detached=True, mat_cache=mat_cache):
                _emit(pl)
                cnt += 1
                if cnt % 2000 == 0:
                    out.write("\n".join(lines) + "\n")
                    lines.clear()
                    if progress_callback is not None:
                        progress_callback(min(cnt, n_shells), n_shells)
            if lines:
                out.write("\n".join(lines) + "\n")
                lines.clear()
            if progress_callback is not None:
                progress_callback(n_shells, n_shells)

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

        # Shared colour/surface-style entities — one set per distinct plate
        # colour, referenced by the per-plate IfcStyledItems emitted above.
        for (r, g, b, tr), (cid, shid, stid) in emitter.color_styles.items():
            out.write(f"#{cid}=IfcColourRgb($,{_f(r)},{_f(g)},{_f(b)});\n")
            out.write(f"#{shid}=IfcSurfaceStyleShading(#{cid},{_f(tr)});\n")
            out.write(f"#{stid}=IfcSurfaceStyle($,.BOTH.,(#{shid}));\n")

        out.write("ENDSEC;\nEND-ISO-10303-21;\n")
    finally:
        out.close()

    if skipped:
        logger.warning(f"streaming IFC: {skipped} plate(s) skipped (geometry not emittable)")
    logger.info(f"streaming IFC write complete: {total} plates + {len(others)} other objects")
