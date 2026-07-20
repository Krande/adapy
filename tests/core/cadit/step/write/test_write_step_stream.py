"""Streaming AP242 STEP writer (Part.to_stp(writer="stream")).

Validation routes through the CAD backend abstraction (active_backend) so it
runs under OpenCASCADE or adacpp -- the streaming writer itself touches no kernel.
"""

import ada
from ada import Beam, Plate, Section


def _roundtrip_solids(path):
    """Read a STEP file via the active CAD backend and return (n_solids, n_invalid)."""
    from ada.cad import active_backend

    be = active_backend()
    shape = be.read_step_bytes(open(path, "rb").read())
    solids = be.solids(shape)
    n_invalid = sum(0 if be.is_valid(s) else 1 for s in solids)
    return len(solids), n_invalid


def _roundtrip_names(path):
    """Read member names back via the XCAF step reader (the assembly tree)."""
    from ada.cad.doc import active_doc_backend

    store = active_doc_backend().step_reader(str(path))
    return {shp.name for shp in store.iter_all_shapes(True)}


def _hierarchy_edges(path):
    """Parse the streamed STEP's assembly tree into (parent_name, child_name) edges
    by resolving each NEXT_ASSEMBLY_USAGE_OCCURRENCE's parent/child PRODUCT_DEFINITION
    back to its PRODUCT name."""
    import re

    txt = open(path).read()
    prods = dict(re.findall(r"#(\d+)\s*=\s*PRODUCT\('([^']*)'", txt))
    pd_to_pdf = dict(re.findall(r"#(\d+)\s*=\s*PRODUCT_DEFINITION\('[^']*','[^']*',#(\d+)", txt))
    pdf_to_prod = dict(re.findall(r"#(\d+)\s*=\s*PRODUCT_DEFINITION_FORMATION\('[^']*','[^']*',#(\d+)\)", txt))

    def name_of_pd(pd):
        return prods.get(pdf_to_prod.get(pd_to_pdf.get(pd)))

    edges = set()
    for m in re.finditer(r"NEXT_ASSEMBLY_USAGE_OCCURRENCE\('[^']*','[^']*','',#(\d+),#(\d+),", txt):
        edges.add((name_of_pd(m.group(1)), name_of_pd(m.group(2))))
    return edges


def test_stream_writer_preserves_assembly_hierarchy(tmp_path):
    # A nested Assembly/Part tree must round-trip its parent hierarchy through the
    # streamed STEP (NEXT_ASSEMBLY_USAGE_OCCURRENCE tree), not land flat under the
    # root — each member sits under its real owning part.
    sub2 = ada.Part("sub2") / [
        Beam("bm2", (0, 0, 0), (0, 0, 3), Section("s2", from_str="IPE300")),
        Plate("pl2", [(0, 0), (1, 0), (1, 1), (0, 1)], 0.02),
    ]
    sub1 = ada.Part("sub1") / [Beam("bm1", (1, 0, 0), (1, 0, 3), Section("s1", from_str="IPE300")), sub2]
    a = ada.Assembly("root") / sub1

    out = tmp_path / "hier.stp"
    stats = a.to_stp(out, writer="stream")
    assert stats == {"emitted": 3, "skipped": 0}

    # member products all present
    names = _roundtrip_names(out)
    assert {"bm1", "bm2", "pl2"}.issubset(names)

    # the exact nested hierarchy is carried in the STEP assembly tree
    edges = _hierarchy_edges(out)
    assert ("root", "sub1") in edges
    assert ("sub1", "sub2") in edges
    assert ("sub1", "bm1") in edges
    assert ("sub2", "bm2") in edges
    assert ("sub2", "pl2") in edges
    # nothing lands flat directly under root except the top sub-assembly
    assert {c for p, c in edges if p == "root"} == {"sub1"}


def _model():
    tub = Beam("tub", (0, 0, 0), (0, 0, 3), Section("tub", from_str="TUB300x20"))  # hollow circle
    box = Beam("box", (1, 0, 0), (1, 0, 3), Section("box", from_str="BOX400x400x20x20"))
    ipe = Beam("ipe", (2, 0, 0), (6, 0, 0), Section("ipe", from_str="IPE300"))  # poly + fillets
    pl = Plate("pl", [(0, 0), (2, 0), (2, 1), (0, 1)], 0.02)
    return ada.Assembly("m") / (ada.Part("pp") / [tub, box, ipe, pl])


def test_stream_writer_emits_valid_solids(tmp_path):
    a = _model()
    out = tmp_path / "stream.stp"
    stats = a.to_stp(out, writer="stream")

    assert stats == {"emitted": 4, "skipped": 0}
    assert out.exists() and out.stat().st_size > 0

    n_solids, n_invalid = _roundtrip_solids(out)
    assert n_solids == 4
    assert n_invalid == 0


def test_stream_writer_member_names_roundtrip(tmp_path):
    a = _model()
    out = tmp_path / "stream_named.stp"
    a.to_stp(out, writer="stream")

    names = _roundtrip_names(out)
    assert {"tub", "box", "ipe", "pl"}.issubset(names)


def test_stream_writer_ap214_schema(tmp_path):
    a = _model()
    out = tmp_path / "stream214.stp"
    a.to_stp(out, writer="stream", schema="AP214")
    assert "AUTOMOTIVE_DESIGN" in out.read_text()
    n_solids, n_invalid = _roundtrip_solids(out)
    assert n_solids == 4 and n_invalid == 0


def test_stream_writer_rejects_unknown(tmp_path):
    a = _model()
    import pytest

    with pytest.raises(ValueError):
        a.to_stp(tmp_path / "x.stp", writer="bogus")


def test_stream_writer_emits_brep_shapes(tmp_path):
    # Beyond extrusions: the writer also emits arbitrary B-rep shapes (ClosedShell /
    # ShellBasedSurfaceModel with analytic faces) via add_brep. Read a stream-emitted
    # model back as B-rep Shapes, re-emit them, and confirm a clean round-trip:
    # every shape streams back AND the re-emitted solids are watertight (edges are
    # shared across adjacent faces, so OCC reads valid solids — no free shells).
    from ada.cadit.step.read.stream_reader import stream_read_step

    a = _model()
    first = tmp_path / "first.stp"
    a.to_stp(first, writer="stream")

    shapes = ada.from_step(first, reader="auto")  # Shapes carrying ClosedShell/SBSM geom
    second = tmp_path / "second.stp"
    stats = shapes.to_stp(second, writer="stream")  # exercises add_brep

    assert stats == {"emitted": 4, "skipped": 0}

    # streams back as the same number of geometry roots
    assert len(list(stream_read_step(second, local_pool=False))) == 4

    # OCC reads every re-emitted solid as a WATERTIGHT solid: edges are shared by
    # EdgeCurve identity, so a circle's two arcs stay distinct (no over-share) and a
    # hollow section keeps its void — same solid count as the source, none invalid.
    n_first, _ = _roundtrip_solids(first)
    n_second, n_invalid = _roundtrip_solids(second)
    assert n_invalid == 0
    assert n_second == n_first


def test_stream_writer_box_and_cylinder_primitives(tmp_path):
    # All four CSG primitives emit as watertight ANALYTIC solids: Box + Cylinder are
    # extrusions (rectangle / circle swept by a length); Cone + Sphere have an exact
    # analytic B-rep (a CONICAL_SURFACE + planar cap, a single SPHERICAL_SURFACE face)
    # built kernel-free — never tessellated.
    a = ada.Assembly("m") / (
        ada.Part("p")
        / [
            ada.PrimBox("bx", (0, 0, 0), (0.5, 0.6, 0.7)),
            ada.PrimCyl("cy", (2, 0, 0), (2, 0, 1), 0.4),
            ada.PrimCone("cn", (4, 0, 0), (4, 0, 1), 0.5),
            ada.PrimSphere("sp", (6, 0, 0), 0.5),
        ]
    )
    out = tmp_path / "prims.stp"
    stats = a.to_stp(out, writer="stream")

    assert stats == {"emitted": 4, "skipped": 0}  # all four emit analytically

    n_solids, n_invalid = _roundtrip_solids(out)
    assert n_solids == 4
    assert n_invalid == 0

    txt = out.read_text()
    # analytic surfaces, not a facet soup: a whole sphere is ONE spherical face
    # bounded by a single pole vertex-loop; the cone carries a conical surface.
    assert txt.count("SPHERICAL_SURFACE(") == 1
    assert txt.count("VERTEX_LOOP(") == 1
    assert txt.count("CONICAL_SURFACE(") == 1
    # the sphere never degrades to thousands of planar triangle faces
    assert txt.count("ADVANCED_FACE(") < 40


def test_stream_writer_bspline_plate_is_an_analytic_valid_solid(tmp_path):
    """A plate whose boundary follows a B-spline emits a real SURFACE_OF_LINEAR_EXTRUSION side face
    (an analytic swept B-spline, NOT sampled into a fan of planar facets), and OCC reads it back as a
    single valid solid. This is the guard for the ap242 analytic B-spline edge path."""
    from ada.api.curves import CurvePoly2d, SplineEdge
    from ada.geom.curves import BSplineCurveFormEnum, BSplineCurveWithKnots, KnotType

    spline = BSplineCurveWithKnots(
        degree=2,
        control_points_list=[(1, 0, 0), (1.3, 0.5, 0), (1, 1, 0)],  # bulges out in +x
        curve_form=BSplineCurveFormEnum.UNSPECIFIED,
        closed_curve=False,
        self_intersect=False,
        knot_multiplicities=[3, 3],
        knots=[0.0, 1.0],
        knot_spec=KnotType.UNSPECIFIED,
    )
    segs = CurvePoly2d.build_edge_segments(
        [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)], [SplineEdge(a=(1, 0, 0), b=(1, 1, 0), curve=spline)]
    )
    pl = Plate.from_segments("bspline_pl", segs, 0.05)

    out = tmp_path / "spline.stp"
    stats = (ada.Assembly("root") / (ada.Part("p") / pl)).to_stp(out, writer="stream")
    assert stats == {"emitted": 1, "skipped": 0}

    n_solids, n_invalid = _roundtrip_solids(out)
    assert (n_solids, n_invalid) == (1, 0)

    txt = out.read_text()
    assert txt.count("SURFACE_OF_LINEAR_EXTRUSION(") == 1  # one analytic swept side face for the spline
    assert "B_SPLINE_CURVE_WITH_KNOTS(" in txt
    # the spline never explodes into a sampled fan of planar side faces (3 lines + 1 spline + 2 caps)
    assert txt.count("ADVANCED_FACE(") == 6
