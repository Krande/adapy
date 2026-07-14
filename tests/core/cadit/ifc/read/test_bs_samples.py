"""Regressions from the corpus audit sweep over the buildingSMART sample set
(files under files/ifc_files/bs_samples/, all public buildingSMART samples).

Each file previously errored (or silently dropped geometry) across every
conversion target:

* inch units (unit scale 0.0254) raised NotImplementedError at read
* IfcTriangulatedFaceSet imported but tessellated to an empty scene
* type-library files (geometry only on an IfcTypeProduct RepresentationMap,
  no placed products) imported zero objects
* IfcRectangleProfileDef swept profiles had no arbitrary-profile conversion
* IfcTrimmedCurve parameter trims (degrees AND radians files) were rejected,
  and the PartialEllipse column crashed the 2D-placement ellipse reader
"""

import pytest

import ada


def _read(example_files, name, monkeypatch):
    monkeypatch.setenv("ADA_IFC_IMPORT_SHAPE_GEOM", "true")
    ada.config.Config().reload_config()
    return ada.from_ifc(example_files / f"ifc_files/bs_samples/{name}")


def _glb_tri_count(a, tmp_path) -> int:
    import trimesh

    glb = tmp_path / "out.glb"
    a.to_gltf(glb)
    scene = trimesh.load(glb)
    return sum(len(g.faces) for g in scene.geometry.values() if hasattr(g, "faces"))


def test_inch_units_triangulated_column(example_files, monkeypatch, tmp_path):
    # Inch-unit file (scale 0.0254): read must convert instead of raising, and
    # the IfcTriangulatedFaceSet body must reach the GLB as a real mesh
    # (direct mesh path — no kernel build exists for it).
    a = _read(example_files, "column-straight-rectangle-tessellation.ifc", monkeypatch)
    objects = list(a.get_all_physical_objects())
    assert len(objects) == 1
    # 8"x8" column, 10ft tall — converted to meters.
    geom = objects[0].geom.geometry
    assert max(abs(float(c)) for p in geom.coordinates for c in p) == pytest.approx(3.048)
    assert _glb_tri_count(a, tmp_path) == 12


def test_type_library_texture_file(example_files, monkeypatch, tmp_path):
    # Geometry lives on an IfcBoilerType RepresentationMap; no placed product
    # exists. The instance-less type import must pick it up.
    a = _read(example_files, "tessellation-with-image-texture.ifc", monkeypatch)
    objects = list(a.get_all_physical_objects())
    assert len(objects) == 1
    assert _glb_tri_count(a, tmp_path) > 0


def test_rectangle_profile_extrusion(example_files, monkeypatch, tmp_path):
    a = _read(example_files, "extruded-solid.ifc", monkeypatch)
    objects = list(a.get_all_physical_objects())
    assert len(objects) == 1
    assert _glb_tri_count(a, tmp_path) > 0
    # The AP242 stream writer converts the parametric profile too.
    out = tmp_path / "out.stp"
    a.to_stp(str(out), writer="stream", fuse_fem=False)
    assert out.stat().st_size > 0


@pytest.mark.parametrize("unit", ["degrees", "radians"])
def test_trimmed_curve_parameters(example_files, monkeypatch, tmp_path, unit):
    # Parameter trims: line basis (IfcVector magnitude scales t), circle basis
    # (angle in the file's plane-angle unit), ellipse basis (2D placement +
    # sampled arc). All three columns must import and tessellate.
    a = _read(example_files, f"curve-parameters-in-{unit}.ifc", monkeypatch)
    objects = list(a.get_all_physical_objects())
    assert len(objects) == 3
    assert _glb_tri_count(a, tmp_path) > 0


def test_trimmed_curve_degrees_radians_parity(example_files, monkeypatch, tmp_path):
    # The two files describe the same geometry in different plane-angle units —
    # after read-time normalization they must produce identical meshes.
    import numpy as np
    import trimesh

    bounds = []
    tris = []
    for unit in ("degrees", "radians"):
        a = _read(example_files, f"curve-parameters-in-{unit}.ifc", monkeypatch)
        glb = tmp_path / f"{unit}.glb"
        a.to_gltf(glb)
        scene = trimesh.load(glb)
        bounds.append(scene.bounds)
        tris.append(sum(len(g.faces) for g in scene.geometry.values() if hasattr(g, "faces")))
    assert tris[0] == tris[1]
    assert np.allclose(bounds[0], bounds[1], atol=1e-6)
