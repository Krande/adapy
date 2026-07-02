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
