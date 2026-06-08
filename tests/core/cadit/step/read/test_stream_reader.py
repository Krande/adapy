"""Kernel-free streaming STEP reader (ada.cadit.step.read.stream_reader).

Round-trips the streaming AP242 writer: emit a model -> stream-read it back into
adapy Geometry instances -> tessellate each via the CAD backend abstraction
(active_backend), so it runs under OpenCASCADE or adacpp. The reader itself
touches no kernel.
"""

import pathlib

import pytest

import ada
from ada import Beam, Plate, Section
from ada.cadit.step.read.stream_reader import stream_read_step
from ada.geom.curves import EdgeCurve, EdgeLoop, Line, OrientedEdge
from ada.geom.surfaces import AdvancedFace, ClosedShell, CylindricalSurface, FaceBound, Plane


def _model():
    # Mirrors tests/core/cadit/step/write/test_write_step_stream.py::_model
    tub = Beam("tub", (0, 0, 0), (0, 0, 3), Section("tub", from_str="TUB300x20"))  # hollow circle -> cylinders
    box = Beam("box", (1, 0, 0), (1, 0, 3), Section("box", from_str="BOX400x400x20x20"))
    ipe = Beam("ipe", (2, 0, 0), (6, 0, 0), Section("ipe", from_str="IPE300"))
    pl = Plate("pl", [(0, 0), (2, 0), (2, 1), (0, 1)], 0.02)
    return ada.Assembly("m") / (ada.Part("pp") / [tub, box, ipe, pl])


def _emit(tmp_path):
    out = tmp_path / "stream.stp"
    stats = _model().to_stp(out, writer="stream")
    assert stats == {"emitted": 4, "skipped": 0}
    return out


def test_stream_reader_yields_one_geometry_per_solid(tmp_path):
    out = _emit(tmp_path)

    geos = list(stream_read_step(out))

    assert len(geos) == 4
    assert {g.id for g in geos} == {"tub", "box", "ipe", "pl"}
    # Every solid is a ClosedShell of AdvancedFaces with analytic surfaces.
    for g in geos:
        assert isinstance(g.geometry, ClosedShell)
        assert len(g.geometry.cfs_faces) > 0
        for face in g.geometry.cfs_faces:
            assert isinstance(face, AdvancedFace)
            assert isinstance(face.face_surface, (Plane, CylindricalSurface))
            assert all(isinstance(b, FaceBound) for b in face.bounds)


def test_stream_reader_reconstructs_topology(tmp_path):
    out = _emit(tmp_path)
    geos = {g.id: g for g in stream_read_step(out)}

    # The hollow tube has both planar (annular caps) and cylindrical (walls) faces.
    tub_surfs = {type(f.face_surface).__name__ for f in geos["tub"].geometry.cfs_faces}
    assert tub_surfs == {"Plane", "CylindricalSurface"}

    # The plate is all planar.
    assert {type(f.face_surface).__name__ for f in geos["pl"].geometry.cfs_faces} == {"Plane"}

    # Drill into one face: AdvancedFace -> FaceBound -> EdgeLoop -> OrientedEdge -> EdgeCurve(Line)
    face = geos["pl"].geometry.cfs_faces[0]
    loop = face.bounds[0].bound
    assert isinstance(loop, EdgeLoop)
    assert len(loop.edge_list) >= 3
    oe = loop.edge_list[0]
    assert isinstance(oe, OrientedEdge)
    assert isinstance(oe.edge_element, EdgeCurve)
    assert isinstance(oe.edge_element.edge_geometry, Line)


def test_stream_reader_vertices_match_plate_corners(tmp_path):
    # A flat plate's cap loop must reproduce the authored corner coordinates.
    pl = Plate("pl", [(0, 0), (2, 0), (2, 1), (0, 1)], 0.02)
    out = tmp_path / "plate.stp"
    (ada.Assembly("m") / (ada.Part("pp") / pl)).to_stp(out, writer="stream")

    (geom,) = list(stream_read_step(out))
    xs, ys = set(), set()
    for face in geom.geometry.cfs_faces:
        for fb in face.bounds:
            for oe in fb.bound.edge_list:
                for p in (oe.start, oe.end):
                    xs.add(round(float(p[0]), 6))
                    ys.add(round(float(p[1]), 6))
    # The two in-plane extents 0 and 2 (x) and 0 and 1 (y) must be present.
    assert {0.0, 2.0}.issubset(xs)
    assert {0.0, 1.0}.issubset(ys)


def test_stream_reader_geometries_tessellate(tmp_path):
    # Each yielded Geometry feeds straight into the active CAD backend. Runs under
    # OCC and adacpp, so it stays on the backend-neutral Mesh contract (.positions;
    # OCC's TriangleMesh.faces is not portable). A geom type a backend hasn't ported
    # yet (e.g. adacpp's cylindrical AdvancedFace -> NotImplementedError) is skipped;
    # the planar solids must tessellate on every backend.
    from ada.cad import active_backend

    out = _emit(tmp_path)
    be = active_backend()

    built = set()
    for g in stream_read_step(out):
        try:
            shp = be.build(g)
            mesh = be.tessellate(shp)
        except NotImplementedError:
            continue  # geom not ported to this backend yet
        assert len(mesh.positions) > 0
        built.add(g.id)

    # planar solids tessellate everywhere; the cylindrical tube also on OCC
    assert {"pl", "box", "ipe"}.issubset(built)
    assert "tub" in built or getattr(be, "name", "") == "adacpp"


def test_stream_reader_local_pool_streams_all_solids(tmp_path):
    # local_pool=True clears the entity pool at each solid boundary; we must
    # still get every solid (the emitter writes each closure contiguously).
    out = _emit(tmp_path)
    assert len(list(stream_read_step(out, local_pool=True))) == 4
    assert len(list(stream_read_step(out, local_pool=False))) == 4


def test_stream_reader_two_pass_handles_forward_references(tmp_path):
    # OpenCASCADE and most writers emit forward references (a solid written before
    # its shell/faces/points) — the opposite of the streaming emitter's bottom-up
    # order. Build a forward-reference file deterministically by reversing the
    # emitter's (bottom-up) DATA statements, so it stays in the reader's analytic
    # vocabulary and doesn't depend on which writer/CAD backend is active.
    out = _emit(tmp_path)
    lines = out.read_text().splitlines()
    di = lines.index("DATA;")
    ei = lines.index("ENDSEC;", di)
    entities = [ln for ln in lines[di + 1 : ei] if ln.strip()]
    forward = lines[: di + 1] + list(reversed(entities)) + lines[ei:]
    fwd = tmp_path / "forward.step"
    fwd.write_text("\n".join(forward) + "\n")

    # single forward pass can't resolve forward references -> nothing
    assert len(list(stream_read_step(fwd, local_pool=True))) == 0
    # two-pass loads the full table first -> reads every solid
    geos = list(stream_read_step(fwd, local_pool=False))
    assert len(geos) == 4
    for g in geos:
        assert isinstance(g.geometry, ClosedShell)
        assert len(g.geometry.cfs_faces) > 0


def test_stream_reader_analytic_surface_coverage(tmp_path):
    # Beyond plane/cylinder: a cone (CONICAL_SURFACE) and a pipe elbow
    # (TOROIDAL_SURFACE) must stream-read and tessellate. The OCC writer emits
    # forward references, so use the two-pass reader.
    from ada.cad import active_backend
    from ada.geom.surfaces import ConicalSurface, ToroidalSurface

    cone = ada.PrimCone("cone", (0, 0, 0), (0, 0, 1), 0.5)
    pipe = ada.Pipe("pipe", [(2, 0, 0), (2, 0, 2), (4, 0, 2)], "PIPE200x10")  # elbow -> torus
    a = ada.Assembly("m") / (ada.Part("p") / [cone, pipe])
    out = tmp_path / "analytic.step"
    a.to_stp(out)  # OCC writer

    geos = list(stream_read_step(out, local_pool=False))
    surfs = {type(f.face_surface).__name__ for g in geos for f in g.geometry.cfs_faces}
    assert ConicalSurface.__name__ in surfs
    assert ToroidalSurface.__name__ in surfs

    be = active_backend()
    for g in geos:
        mesh = be.tessellate(be.build(g))
        assert len(mesh.positions) > 0


def test_stream_reader_flavor_agnostic(tmp_path):
    # The geometry/topology entities are identical across AP203/AP214/AP242; the
    # reader ignores the header FILE_SCHEMA. An AP214 file must read like AP242.
    out = tmp_path / "ap214.step"
    _model().to_stp(out, writer="stream", schema="AP214")
    assert "AUTOMOTIVE_DESIGN" in out.read_text()  # AP214 header marker
    assert len(list(stream_read_step(out))) == 4


def test_stream_reader_sphere_falls_back_to_occ(tmp_path):
    # A sphere is closed in u and v; the reader signals it unsupported so
    # reader="auto" falls back to OCC (reads everything, no crash), while
    # reader="stream" raises rather than producing a crashing degenerate face.
    from ada.cadit.step.read.stream_reader import StepStreamUnsupported

    sph = ada.PrimSphere("sph", (0, 0, 0), 0.5)
    out = tmp_path / "sphere.step"
    (ada.Assembly("m") / (ada.Part("p") / sph)).to_stp(out)

    import pytest

    with pytest.raises(StepStreamUnsupported):
        list(stream_read_step(out, local_pool=False))

    asm = ada.from_step(out, reader="auto")  # OCC fallback, must not crash
    assert len(list(asm.get_all_physical_objects())) == 1


def test_stream_reader_complex_rational_bspline_curve():
    # The STEP complex-instance form of a rational B-spline curve must parse into a
    # RationalBSplineCurveWithKnots (self-contained; no writer/backend involved).
    from ada.cadit.step.read.stream_reader import _Rec, _Resolver, _parse_statement
    from ada.geom.curves import RationalBSplineCurveWithKnots

    stmts = [
        "#1=CARTESIAN_POINT('',(0.,0.,0.))",
        "#2=CARTESIAN_POINT('',(1.,0.,0.))",
        "#3=CARTESIAN_POINT('',(2.,1.,0.))",
        "#4=(B_SPLINE_CURVE(2,(#1,#2,#3),.UNSPECIFIED.,.F.,.F.)"
        "B_SPLINE_CURVE_WITH_KNOTS((3,3),(0.,1.),.UNSPECIFIED.)"
        "RATIONAL_B_SPLINE_CURVE((1.,0.8,1.))BOUNDED_CURVE()"
        "REPRESENTATION_ITEM('')GEOMETRIC_REPRESENTATION_ITEM()CURVE())",
    ]
    pool = {}
    for s in stmts:
        pid, etype, args = _parse_statement(s)
        pool[pid] = _Rec(etype, args)

    curve = _Resolver(pool).resolve(4)
    assert isinstance(curve, RationalBSplineCurveWithKnots)
    assert curve.degree == 2
    assert [tuple(p) for p in curve.control_points_list] == [(0, 0, 0), (1, 0, 0), (2, 1, 0)]
    assert curve.knot_multiplicities == [3, 3]
    assert curve.weights_data == [1.0, 0.8, 1.0]


def test_stream_reader_bspline_surface_and_pure_shell(example_files):
    # A non-rational B-spline surface, re-exported as a pure (thickness-less) shell,
    # must stream-read: the root is a shell/surface-model and the face carries a
    # BSplineSurfaceWithKnots. (Imports via the active doc backend, so runs under
    # OCC and adacpp.)
    import tempfile

    from ada.geom.surfaces import (
        BSplineSurfaceWithKnots,
        ClosedShell,
        OpenShell,
        ShellBasedSurfaceModel,
    )

    a = ada.from_step(example_files / "step_files/bsplinesurfacewithknots.stp", reader="occ")
    out = pathlib.Path(tempfile.mkdtemp()) / "re.step"
    a.to_stp(out)

    geos = list(stream_read_step(out, local_pool=False))
    assert len(geos) >= 1
    assert all(isinstance(g.geometry, (ShellBasedSurfaceModel, OpenShell, ClosedShell)) for g in geos)

    def _faces(geo):
        if isinstance(geo, ShellBasedSurfaceModel):
            return [f for sh in geo.sbsm_boundary for f in sh.cfs_faces]
        return geo.cfs_faces

    surf_types = {type(f.face_surface).__name__ for g in geos for f in _faces(g.geometry)}
    assert BSplineSurfaceWithKnots.__name__ in surf_types


def test_stream_reader_rational_bspline_falls_back(example_files):
    # Rational B-spline faces need p-curve trimming; the reader signals unsupported
    # (reader="stream" raises) so reader="auto" falls back to OCC and still renders.
    import tempfile

    from ada.cadit.step.read.stream_reader import StepStreamUnsupported

    a = ada.from_step(example_files / "step_files/curved_plate.stp", reader="occ")
    out = pathlib.Path(tempfile.mkdtemp()) / "rat.step"
    a.to_stp(out)

    with pytest.raises(StepStreamUnsupported):
        list(stream_read_step(out, local_pool=False))

    asm = ada.from_step(out, reader="auto")  # OCC fallback, no crash/empty
    assert len(list(asm.get_all_physical_objects())) >= 1
