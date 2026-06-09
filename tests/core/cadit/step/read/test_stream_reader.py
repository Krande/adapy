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


def test_stream_reader_lazy_offset_pool_matches_dict_pool(tmp_path):
    # Large files resolve against an mmap + offset-index pool (parse each entity on
    # demand) instead of a parsed-entity dict, to stay within a worker pod's memory
    # budget (a 750 MB CAD assembly is 5+ GB as a dict pool, ~2 GB lazy). Force the
    # lazy path on a small file and assert it yields exactly the same solids.
    from ada.cadit.step.read import stream_reader as sr

    out = _emit(tmp_path)

    def sig(gen):
        return sorted(
            (g.id, type(g.geometry).__name__, len(g.geometry.cfs_faces)) for g in gen
        )

    eager = sig(sr._read_two_pass(out, low_memory=False))
    lazy = sig(sr._read_two_pass(out, low_memory=True))
    assert lazy == eager
    assert len(lazy) == 4


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
    # Parse coverage is backend-independent:
    assert ConicalSurface.__name__ in surfs
    assert ToroidalSurface.__name__ in surfs

    # Tessellation is asserted strictly under OCC (the reference backend); under
    # adacpp it is best-effort — a face it hasn't ported (NotImplementedError) or
    # can't build yet (RuntimeError, e.g. a seam-bounded cylindrical wire) is
    # skipped. adacpp seam/periodic-face robustness is a follow-up.
    be = active_backend()
    strict = getattr(be, "name", "") == "occ"
    for g in geos:
        try:
            mesh = be.tessellate(be.build(g))
        except (NotImplementedError, RuntimeError):
            if strict:
                raise
            continue
        assert len(mesh.positions) > 0


def test_stream_reader_flavor_agnostic(tmp_path):
    # The geometry/topology entities are identical across AP203/AP214/AP242; the
    # reader ignores the header FILE_SCHEMA. An AP214 file must read like AP242.
    out = tmp_path / "ap214.step"
    _model().to_stp(out, writer="stream", schema="AP214")
    assert "AUTOMOTIVE_DESIGN" in out.read_text()  # AP214 header marker
    assert len(list(stream_read_step(out))) == 4


def test_stream_reader_sphere_builds_full_face(tmp_path):
    # A complete sphere is closed in u AND v, so OCCT bounds it with a single
    # degenerate VERTEX_LOOP (no edges) -> an empty-bounds AdvancedFace. The reader
    # reconstructs the SphericalSurface and the OCC face builder makes the natural full
    # sphere from the surface (OCC adds the seam + poles), so it streams AND tessellates
    # instead of crashing the mesher on a reconstructed seam.
    from ada.cad import active_backend
    from ada.geom.surfaces import SphericalSurface

    sph = ada.PrimSphere("sph", (0, 0, 0), 0.5)
    out = tmp_path / "sphere.step"
    (ada.Assembly("m") / (ada.Part("p") / sph)).to_stp(out)

    (geom,) = list(stream_read_step(out, local_pool=False))
    surfs = {type(f.face_surface).__name__ for f in geom.geometry.cfs_faces}
    assert surfs == {SphericalSurface.__name__}

    be = active_backend()
    try:
        mesh = be.tessellate(be.build(geom))
    except NotImplementedError:
        return  # analytic sphere face not ported to this backend yet (e.g. adacpp)
    import numpy as np

    pos = np.asarray(mesh.positions).reshape(-1, 3)
    assert len(pos) > 0  # a real triangulated sphere, not a degenerate/empty face


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


def test_stream_reader_tolerant_skips_unsupported(example_files, tmp_path):
    # tolerant mode reads every supported solid and SKIPS the unsupported ones (a
    # rational-B-spline curved plate here — still needs p-curve trimming) instead of
    # raising, so a big mixed CAD file reads its supported solids kernel-free rather
    # than dropping the whole file to OCC.
    from ada.cadit.step.read.stream_reader import StepStreamUnsupported

    a = ada.from_step(example_files / "step_files/curved_plate.stp", reader="occ")  # rational B-spline
    a / (ada.Part("extra") / Beam("box", (10, 0, 0), (10, 0, 3), Section("box", from_str="BOX400x400x20x20")))
    out = tmp_path / "mix.step"
    a.to_stp(out)  # OCC writer; rational-B-spline plate + analytic box

    # non-tolerant two-pass raises on the unsupported rational-B-spline plate
    with pytest.raises(StepStreamUnsupported):
        list(stream_read_step(out, local_pool=False))

    # tolerant skips the rational plate and reads the analytic box (no raise, no OCC)
    tol = list(stream_read_step(out, local_pool=False, tolerant=True))
    assert len(tol) >= 1
    assert all(isinstance(g.geometry, ClosedShell) for g in tol)

    # from_step(reader="tolerant"): no OCC fallback; the box is imported
    asm = ada.from_step(out, reader="tolerant")
    assert len(list(asm.get_all_physical_objects())) >= 1


def test_stream_reader_curved_faces_full_coverage(tmp_path):
    # Closed cylinder/cone/torus faces (full circle + seam) and near-degenerate arc
    # slivers must ALL build into OCC faces, not drop — 100% face coverage on curved
    # CAD. Exercises _try_make_closed_revolution_face (parametric-bounds seam faces)
    # and the chord fallback for sub-mm arcs. OCC-only (make_face_from_geom).
    pytest.importorskip("OCC.Core.BRepBuilderAPI")
    from ada.occ.geom.surfaces import make_face_from_geom

    a = ada.Assembly("m") / (
        ada.Part("p")
        / [
            ada.PrimCyl("cy", (0, 0, 0), (0, 0, 1), 0.4),
            ada.PrimCone("cn", (2, 0, 0), (2, 0, 1), 0.5),
            ada.Pipe("pi", [(4, 0, 0), (4, 0, 2), (6, 0, 2)], "PIPE200x10"),  # cyl + torus elbow
        ]
    )
    out = tmp_path / "curved.step"
    a.to_stp(out)  # OCC writer

    built = dropped = 0
    for g in stream_read_step(out, local_pool=False, tolerant=True):
        for face in g.geometry.cfs_faces:
            try:
                occ_face = make_face_from_geom(face)
                built += 1 if occ_face is not None and not occ_face.IsNull() else 0
                dropped += 0 if occ_face is not None and not occ_face.IsNull() else 1
            except Exception:
                dropped += 1
    assert built > 0
    assert dropped == 0
