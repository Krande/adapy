"""Kernel-free streaming STEP reader (ada.cadit.step.read.stream_reader).

Round-trips the streaming AP242 writer: emit a model -> stream-read it back into
adapy Geometry instances -> tessellate each via the CAD backend abstraction
(active_backend), so it runs under OpenCASCADE or adacpp. The reader itself
touches no kernel.
"""

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
