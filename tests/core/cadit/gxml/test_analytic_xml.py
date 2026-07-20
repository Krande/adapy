"""FEM-shell → Genie-XML analytic export.

A curved shell mesh (a tubular member) folds into a handful of analytic
``<curved_shell>`` faces backed by an embedded ACIS body — the same compact
model FEM→STEP/IFC emit — instead of thousands of coplanar ``<flat_plate>``
polygons. These cover the writer end to end: it emits curved shells with SAT
face references, the result is smaller than the coplanar polygon export, and it
round-trips back to curved (spline-surface) plates without dropping a face.
"""

from __future__ import annotations

import numpy as np

import ada
import ada.geom.surfaces as geo_su
from ada.api.plates import PlateCurved
from ada.fem.formats.mesh_faces import iter_fem_analytic_faces


def _tube(nseg=24, nrows=10, r=0.5, length=4.0, t=0.01):
    """A quad-meshed cylinder along z — a synthetic tubular member."""
    from ada import Node

    p = ada.Part("tube")
    mat = ada.Material("S355")
    grid: dict = {}
    nid = 1
    for iz in range(nrows + 1):
        for ia in range(nseg):
            ang = 2.0 * np.pi * ia / nseg
            grid[(ia, iz)] = Node([r * np.cos(ang), r * np.sin(ang), length * iz / nrows], nid)
            p.fem.nodes.add(grid[(ia, iz)])
            nid += 1
    eid = 1
    quad = ada.fem.Elem.EL_TYPES.SHELL_SHAPES.QUAD
    for iz in range(nrows):
        for ia in range(nseg):
            a_, b_ = grid[(ia, iz)], grid[((ia + 1) % nseg, iz)]
            c_, d_ = grid[((ia + 1) % nseg, iz + 1)], grid[(ia, iz + 1)]
            el = p.fem.add_elem(ada.fem.Elem(eid, [a_, b_, c_, d_], quad, el_formulation_override="S4"))
            el.fem_sec = p.fem.add_section(
                ada.fem.FemSection(f"S{eid}", "shell", ada.fem.FemSet(f"s{eid}", [el]), mat, thickness=t)
            )
            eid += 1
    a = ada.Assembly() / p
    p.fem.sections.merge_by_properties()
    return a


def test_analytic_xml_emits_curved_shell_with_sat(tmp_path):
    out = _tube().to_genie_xml(tmp_path / "cyl.xml", streaming=True, merge_strategy="cylinder")
    txt = out.read_text()
    assert "<curved_shell" in txt
    # the curved shell names SAT faces in the embedded body
    assert 'face_ref="FACE' in txt
    assert "sat_embedded_sequence" in txt


def test_analytic_default_needs_no_explicit_embed_sat(tmp_path):
    # the analytic strategy turns embed_sat on by itself (the curved faces need it)
    out = _tube().to_genie_xml(tmp_path / "cyl.xml", streaming=True, merge_strategy="analytic")
    assert "<curved_shell" in out.read_text()


def test_analytic_smaller_than_coplanar(tmp_path):
    cyl = _tube().to_genie_xml(tmp_path / "cyl.xml", streaming=True, merge_strategy="cylinder")
    cop = _tube().to_genie_xml(tmp_path / "cop.xml", streaming=True, merge_strategy="coplanar")
    assert cyl.stat().st_size < cop.stat().st_size
    # the tube collapses to far fewer structures than the per-facet coplanar merge
    assert cyl.read_text().count("<curved_shell") < cop.read_text().count("<flat_plate")


def test_analytic_xml_drops_no_face(tmp_path):
    """Every analytic face becomes exactly one structure (curved_shell or
    flat_plate) — none silently dropped."""
    n_faces = sum(1 for _ in iter_fem_analytic_faces(_tube()))
    out = _tube().to_genie_xml(tmp_path / "cyl.xml", streaming=True, merge_strategy="cylinder")
    txt = out.read_text()
    assert txt.count("<curved_shell") + txt.count("<flat_plate") == n_faces


def test_analytic_xml_roundtrips_to_curved_plates(tmp_path):
    out = _tube().to_genie_xml(tmp_path / "cyl.xml", streaming=True, merge_strategy="cylinder")
    part = ada.from_genie_xml(out)
    curved = list(part.get_all_physical_objects(by_type=PlateCurved))
    assert len(curved) >= 1
    # the cylinders come back as curved (spline) surfaces, not flattened
    assert all(isinstance(c.geom.geometry.face_surface, geo_su.BSplineSurfaceWithKnots) for c in curved)


def test_roundtripped_cylinder_tessellates_curved(tmp_path):
    """The read-back curved shell tessellates to a real curved surface (its
    pcurves survive the round trip), not a degenerate flat sliver."""
    from ada.occ.tessellating import BatchTessellator

    out = _tube().to_genie_xml(tmp_path / "cyl.xml", streaming=True, merge_strategy="cylinder")
    curved = list(ada.from_genie_xml(out).get_all_physical_objects(by_type=PlateCurved))
    assert curved

    bt = BatchTessellator()
    area = 0.0
    for ms in bt.batch_tessellate(curved):
        pos = np.asarray(ms.get_position3(), dtype=float)
        idx = np.asarray(ms.get_indices3(), dtype=int)
        area += sum(0.5 * np.linalg.norm(np.cross(pos[t[1]] - pos[t[0]], pos[t[2]] - pos[t[0]])) for t in idx)
    # the tube's one-sided lateral area is ~12.6 m^2; a collapsed sliver would be
    # near zero. A comfortably positive area proves the curvature tessellated.
    assert area > 5.0
