import ada
from ada.geom.surfaces import AdvancedFace, OpenShell, ShellBasedSurfaceModel


def _assert_renders(shape, occ_only=False):
    """Tessellate the shape's OCC body and assert it produces triangles. Some of these
    trimmed-surface bodies build a valid B-rep but used to grid to zero triangles until the
    ShapeFix p-curve retry (and, for with_arc, the ShellBasedSurfaceModel builder).

    ``occ_only`` skips under the adacpp backend for ShellBasedSurfaceModel and bspline
    p-curve trimming. The adacpp backend now renders these (AdacppBackend.build sews the
    faces; adacpp.cad.sew_faces + the tessellation ShapeFix p-curve retry), but only once
    the adacpp build carrying those lands in the env — until then the conda adacpp lacks
    sew_faces. Flip these to run under adacpp after that release."""
    import pytest

    from ada.cad import active_backend

    backend = active_backend()
    if occ_only and backend.name == "adacpp" and not hasattr(getattr(backend, "_cad", None), "sew_faces"):
        pytest.skip("adacpp build in this env predates sew_faces / the p-curve tessellation retry")

    from ada.visit.tessellate import BatchTessellator

    ms = BatchTessellator().tessellate_occ_geom(shape.solid_occ(), shape.guid, shape.color)
    assert ms is not None and ms.position is not None and len(ms.position) > 0
    assert ms.indices is not None and len(ms.indices) > 0


def test_import_arc_boundary(example_files, monkeypatch):
    monkeypatch.setenv("ADA_IFC_IMPORT_SHAPE_GEOM", "true")
    ada.config.Config().reload_config()
    a = ada.from_ifc(example_files / "ifc_files/with_arc_boundary.ifc")
    objects = list(a.get_all_physical_objects())
    assert len(objects) == 1
    shape = objects[0]

    assert shape is not None

    geom = shape.geom

    assert geom is not None
    assert isinstance(geom.geometry, ShellBasedSurfaceModel)
    assert len(geom.geometry.sbsm_boundary) == 1
    boundary = geom.geometry.sbsm_boundary[0]

    assert isinstance(boundary, OpenShell)
    assert len(boundary.cfs_faces) == 4

    _assert_renders(shape, occ_only=True)


def test_import_bspline_w_knots(example_files, monkeypatch, tmp_path):
    monkeypatch.setenv("ADA_IFC_IMPORT_SHAPE_GEOM", "true")
    a = ada.from_ifc(example_files / "ifc_files/bsplinesurfacewithknots.ifc")
    objects = list(a.get_all_physical_objects())
    assert len(objects) == 1

    shape = objects[0]
    assert shape.geom is not None

    geom = shape.geom

    assert isinstance(geom.geometry, AdvancedFace)

    _assert_renders(shape, occ_only=True)

    b = ada.Assembly()
    p = b.add_part(ada.Part("MyPart"))
    p.add_material(shape.material.copy_to("S355", p))
    p.add_shape(shape)
    b.to_ifc(tmp_path / "bsplinesurfacewithknots.ifc", validate=True)


def test_import_half_space_beam(example_files, monkeypatch):
    # An I-beam (IfcExtrudedAreaSolid w/ IfcIShapeProfileDef) clipped by two
    # IfcHalfSpaceSolids via nested IfcBooleanClippingResults -- read as the Beam it is, with the
    # two clips as its BoolHalfSpace cuts (read_beams.get_beam_body), natively, not via the
    # IfcOpenShell kernel fallback. Its solid is the file's: the kernel's volume of the product.
    import ifcopenshell
    import ifcopenshell.geom
    import numpy as np
    import pytest

    from ada.cad import active_backend

    monkeypatch.setenv("ADA_IFC_IMPORT_SHAPE_GEOM", "true")
    ada.config.Config().reload_config()
    path = example_files / "ifc_files/half_space_beam.ifc"
    a = ada.from_ifc(path)
    objects = list(a.get_all_physical_objects())
    assert len(objects) == 1
    beam = objects[0]

    assert isinstance(beam, ada.Beam)
    assert len(beam.booleans) == 2  # the two half-space cuts
    assert all(isinstance(b.primitive, ada.BoolHalfSpace) for b in beam.booleans)

    f = ifcopenshell.open(str(path))
    kernel = ifcopenshell.geom.create_shape(ifcopenshell.geom.settings(), f.by_type("IfcBeam")[0]).geometry
    v = np.asarray(kernel.verts, float).reshape(-1, 3)
    tris = np.asarray(kernel.faces, int).reshape(-1, 3)
    kernel_volume = sum(float(np.dot(v[i], np.cross(v[j], v[k]))) for i, j, k in tris) / 6.0
    # Within the kernel's own tessellation of the profile's root fillets.
    assert active_backend().volume(beam.solid_occ()) == pytest.approx(kernel_volume, rel=1e-3)

    _assert_renders(beam)
