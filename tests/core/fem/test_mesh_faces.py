"""Vectorized, object-free FEM-shell -> CAD-face source."""

from __future__ import annotations

import numpy as np
import pytest

import ada
from ada.fem.formats.mesh_faces import FaceData, MergeStrategy, iter_faces


def _poly_area(outline) -> float:
    p = np.asarray(outline, dtype=float)
    n = np.zeros(3)
    for i in range(len(p)):
        n += np.cross(p[i], p[(i + 1) % len(p)])
    return 0.5 * float(np.linalg.norm(n))


def _total_area(faces) -> float:
    return sum(_poly_area(f.outline) for f in faces)


def test_merge_strategy_from_value():
    assert MergeStrategy.from_value(None) is MergeStrategy.NONE
    assert MergeStrategy.from_value(True) is MergeStrategy.COPLANAR
    assert MergeStrategy.from_value(False) is MergeStrategy.NONE
    assert MergeStrategy.from_value("coplanar") is MergeStrategy.COPLANAR
    assert MergeStrategy.from_value(MergeStrategy.NONE) is MergeStrategy.NONE


def test_none_strategy_one_face_per_shell(fem_files):
    a = ada.from_fem(fem_files / "sesam/beamMassT1.FEM")
    faces = list(iter_faces(a, MergeStrategy.NONE))
    assert all(isinstance(f, FaceData) for f in faces)
    # beamMassT1's plate is a 2x2 grid of coplanar quad shells.
    assert len(faces) == 4
    assert _total_area(faces) == pytest.approx(100.0, rel=1e-6)


def test_coplanar_merges_and_conserves_area(fem_files):
    a = ada.from_fem(fem_files / "sesam/beamMassT1.FEM")
    none = list(iter_faces(a, MergeStrategy.NONE))

    b = ada.from_fem(fem_files / "sesam/beamMassT1.FEM")
    coplanar = list(iter_faces(b, MergeStrategy.COPLANAR))

    # the 4 coplanar shells fold into a single plate, same covered area.
    assert len(coplanar) < len(none)
    assert len(coplanar) == 1
    assert _total_area(coplanar) == pytest.approx(_total_area(none), rel=1e-6)


def test_coplanar_geometrically_equivalent_to_object_merge(fem_files):
    # The vectorized merge need not reproduce the object merge byte-for-byte, but
    # it must cover the same surface (same merged regions, within tolerance).
    ref = ada.from_fem(fem_files / "sesam/beamMassT1.FEM")
    ref.create_objects_from_fem(merge=True)
    obj_area = 0.0
    for pl in ref.get_all_physical_objects(by_type=ada.Plate):
        ap = pl.placement.get_absolute_placement(include_rotations=True)
        ident = ada.Placement()
        glob = [
            ap.transform_array_from_other_place(np.asarray([pt], dtype=float), ident, ignore_translation=False)[0]
            for pt in pl.poly.points3d
        ]
        obj_area += _poly_area(glob)

    vec = ada.from_fem(fem_files / "sesam/beamMassT1.FEM")
    vec_area = _total_area(iter_faces(vec, MergeStrategy.COPLANAR))

    assert vec_area == pytest.approx(obj_area, rel=1e-6)


def test_object_free_no_plates_built(fem_files):
    # Consuming the face source must not materialise concept Plate objects on
    # the part (the whole point of the vectorized path).
    a = ada.from_fem(fem_files / "sesam/beamMassT1.FEM")
    list(iter_faces(a, MergeStrategy.COPLANAR))
    assert len(list(a.get_all_physical_objects(by_type=ada.Plate))) == 0


def test_surface_and_panel_not_yet_implemented(fem_files):
    a = ada.from_fem(fem_files / "sesam/beamMassT1.FEM")
    for strat in (MergeStrategy.SURFACE, MergeStrategy.PANEL):
        with pytest.raises(NotImplementedError):
            list(iter_faces(a, strat))


def _part_with_fem(fem_files):
    a = ada.from_fem(fem_files / "sesam/beamMassT1.FEM")
    return a.get_all_parts_in_assembly(include_self=True)[0]


def test_iter_objects_from_fem_strategy_merges_plates(fem_files):
    # The merge strategy lives on the generator: every streaming consumer (Genie
    # XML, IFC, STEP) folds shells the same way through iter_objects_from_fem.
    raw = list(_part_with_fem(fem_files).iter_objects_from_fem(beams=False, plates=True, merge_strategy=None))
    merged = list(_part_with_fem(fem_files).iter_objects_from_fem(beams=False, plates=True, merge_strategy="coplanar"))

    assert all(isinstance(p, ada.Plate) for p in raw + merged)
    assert len(raw) == 4
    assert len(merged) == 1  # 4 coplanar shells fold to one plate object

    def total(plates):
        s = 0.0
        for pl in plates:
            ap = pl.placement.get_absolute_placement(include_rotations=True)
            ident = ada.Placement()
            glob = [
                ap.transform_array_from_other_place(np.asarray([pt], dtype=float), ident, ignore_translation=False)[0]
                for pt in pl.poly.points3d
            ]
            s += _poly_area(glob)
        return s

    assert total(merged) == pytest.approx(total(raw), rel=1e-6)


def test_iter_objects_from_fem_default_unchanged(fem_files):
    # merge_strategy=None must keep the legacy 1:1 element->plate behaviour.
    plates = list(_part_with_fem(fem_files).iter_objects_from_fem(beams=False, plates=True))
    assert len(plates) == 4


def _gable_roof(nx=6, ny=8, w=4.0, h=2.0, ly=6.0, t=0.02, tri_tip=True):
    """Two sloped rectangular planes meeting at a ridge (x=0, z=h) — a gable/tent roof. Exercises the
    key merge behaviours: angled (non-axis) planes, the two slopes as DISTINCT normals, and a mix of
    quad + (optional) triangle elements. z = h*(1 - |x|/w), so each side is one exact plane."""
    from ada import Node

    p = ada.Part("roof")
    mat = ada.Material("S355")
    xs = list(np.linspace(-w, 0, nx + 1)) + list(np.linspace(0, w, nx + 1))[1:]  # shared apex column at x=0

    def zof(x):
        return h * (1.0 - abs(x) / w)

    grid: dict = {}
    nid = 1
    for ix, x in enumerate(xs):
        for iy in range(ny + 1):
            grid[(ix, iy)] = Node([x, ly * iy / ny, zof(x)], nid)
            p.fem.nodes.add(grid[(ix, iy)])
            nid += 1
    eid = 1

    def _add(nodes, shape):
        nonlocal eid
        el = p.fem.add_elem(ada.fem.Elem(eid, nodes, shape, el_formulation_override="S4" if len(nodes) == 4 else "S3"))
        el.fem_sec = p.fem.add_section(
            ada.fem.FemSection(f"S{eid}", "shell", ada.fem.FemSet(f"s{eid}", [el]), mat, thickness=t)
        )
        eid += 1

    Q = ada.fem.Elem.EL_TYPES.SHELL_SHAPES.QUAD
    T = ada.fem.Elem.EL_TYPES.SHELL_SHAPES.TRI
    for ix in range(len(xs) - 1):
        for iy in range(ny):
            a_, b_, c_, d_ = grid[(ix, iy)], grid[(ix + 1, iy)], grid[(ix + 1, iy + 1)], grid[(ix, iy + 1)]
            # split a couple of cells into triangles so the tri/quad-combined merge is exercised
            if tri_tip and iy == 0:
                _add([a_, b_, c_], T)
                _add([a_, c_, d_], T)
            else:
                _add([a_, b_, c_, d_], Q)
    a = ada.Assembly() / p
    p.fem.sections.merge_by_properties()
    return a


def _fem_shell_area(part) -> float:
    total = 0.0
    for p in part.get_all_parts_in_assembly(include_self=True):
        for el in p.fem.elements.shell:
            total += _poly_area([n.p for n in el.nodes])
    return total


def _face_boundary_area(f) -> float:
    poly = getattr(f.bounds[0].bound, "polygon", None)
    if not poly:
        return 0.0
    outer = _poly_area([[pt.x, pt.y, pt.z] for pt in poly])
    holes = sum(
        _poly_area([[q.x, q.y, q.z] for q in getattr(hb.bound, "polygon", [])])
        for hb in f.bounds[1:]
        if getattr(hb.bound, "polygon", None)
    )
    return max(outer - holes, 0.0)


def test_analytic_merge_conserves_area_and_splits_slopes():
    """The DELIVERED merge (iter_fem_analytic_faces — what FEM->STEP/IFC now emit by default) must
    cover the same surface as the source FEM mesh, keep the two roof slopes as SEPARATE flat plates,
    and actually reduce the face count. Backend-independent (boundary-polygon areas, no tessellation)."""
    from ada.fem.formats.mesh_faces import iter_fem_analytic_faces

    a = _gable_roof()
    in_area = _fem_shell_area(a)
    n_shells = sum(len(list(p.fem.elements.shell)) for p in a.get_all_parts_in_assembly(include_self=True))

    faces = list(iter_fem_analytic_faces(a))  # default reconstruct_curved=False → robust flat merge

    # conservation: the merged plates cover the source area (both slopes are exactly flat)
    out_area = sum(_face_boundary_area(f) for f in faces)
    assert out_area == pytest.approx(in_area, rel=0.01), f"merged area {out_area} vs source {in_area}"
    # merging happened — far fewer faces than shell elements
    assert 0 < len(faces) < n_shells / 5
    # the two angled slopes are kept as separate plates (>=2 distinct plane normals)
    normals = {
        tuple(np.round(np.asarray(f.face_surface.position.axis, dtype=float), 2))
        for f in faces
        if hasattr(f.face_surface, "position")
    }
    assert len(normals) >= 2
