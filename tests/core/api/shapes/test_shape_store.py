"""ShapeStore + ShapeProxy: blob-backed lazy shape geometry.

Parity is asserted via re-serialization bytes (NGEOM) or field checks — Geometry
dataclass ``==`` is unusable because Point's ndarray ``__eq__`` breaks dataclass
equality.
"""

from __future__ import annotations

import gc
import pickle
import weakref

import ada.geom.curves as cu
import ada.geom.solids as so
import ada.geom.surfaces as su
from ada.api.primitives.base import Shape
from ada.api.shapes import ShapeProxy, ShapeStore
from ada.cadit.ngeom import serialize_geometries
from ada.geom.booleans import BooleanOperation, BoolOpEnum
from ada.geom.core import Geometry
from ada.geom.placement import Axis2Placement3D, Direction, Point


def _line_oe(s, t):
    ec = cu.EdgeCurve(start=s, end=t, edge_geometry=cu.Line(s, [b - a for a, b in zip(s, t)]), same_sense=True)
    return cu.OrientedEdge(start=s, end=t, edge_element=ec, orientation=True)


def _square_face():
    p = [(0, 0, 0), (2, 0, 0), (2, 2, 0), (0, 2, 0)]
    loop = cu.EdgeLoop(edge_list=[_line_oe(p[i], p[(i + 1) % 4]) for i in range(4)])
    plane = su.Plane(position=Axis2Placement3D(Point(0, 0, 0), Direction(0, 0, 1), Direction(1, 0, 0)))
    return su.FaceSurface(bounds=[su.FaceBound(bound=loop, orientation=True)], face_surface=plane, same_sense=True)


def _shell_geometry(gid="solid1") -> Geometry:
    return Geometry(id=gid, geometry=su.ClosedShell(cfs_faces=[_square_face()]))


def _boolean_geometry(gid="cut1") -> Geometry:
    base = _shell_geometry(gid)
    half_space = su.HalfSpaceSolid(
        base_surface=su.Plane(position=Axis2Placement3D(Point(0, 0, 1), Direction(0, 0, 1), Direction(1, 0, 0)))
    )
    box = so.Box(Axis2Placement3D(Point(0, 0, 0), Direction(0, 0, 1), Direction(1, 0, 0)), 1.0, 1.0, 1.0)
    base.bool_operations = [
        BooleanOperation(Geometry("hs", half_space), BoolOpEnum.DIFFERENCE),
        BooleanOperation(Geometry("box", box), BoolOpEnum.UNION),
    ]
    return base


def _ngeom_blob(geom: Geometry) -> bytes:
    return serialize_geometries([(str(geom.id), geom.geometry)])


def test_ngeom_blob_roundtrip_byte_parity():
    """ngeom-kind: hydrated tree re-serializes to the exact stored bytes."""
    g = _shell_geometry()
    blob = _ngeom_blob(g)
    store = ShapeStore()
    idx = store.add_blob(blob, gid=str(g.id))
    hydrated = store.geometry(idx)
    assert hydrated.id == "solid1"
    assert serialize_geometries([(hydrated.id, hydrated.geometry)]) == blob


def test_add_blob_rejects_non_ngeom():
    store = ShapeStore()
    try:
        store.add_blob(b"not a buffer at all", gid="x")
    except ValueError as e:
        assert "magic" in str(e)
    else:
        raise AssertionError("expected ValueError on bad magic")


def test_pickle_kind_roundtrips_bool_operations():
    """pickle-kind: booleans (incl. half-space operands) hydrate exactly."""
    g = _boolean_geometry()
    store = ShapeStore()
    idx = store.add_geometry(g)
    hydrated = store.geometry(idx)
    assert hydrated.id == "cut1"
    assert isinstance(hydrated.geometry, su.ClosedShell)
    ops = hydrated.bool_operations
    assert [op.operator for op in ops] == [BoolOpEnum.DIFFERENCE, BoolOpEnum.UNION]
    hs = ops[0].second_operand.geometry
    assert isinstance(hs, su.HalfSpaceSolid)
    assert hs.agreement_flag is True
    assert list(hs.base_surface.position.location) == [0.0, 0.0, 1.0]
    box = ops[1].second_operand.geometry
    assert isinstance(box, so.Box)
    assert (box.x_length, box.y_length, box.z_length) == (1.0, 1.0, 1.0)


def test_weakref_cache_identity_and_release():
    store = ShapeStore()
    idx = store.add_geometry(_shell_geometry())
    g1 = store.geometry(idx)
    assert store.geometry(idx) is g1, "same live object while referenced"
    ref = weakref.ref(g1)
    del g1
    gc.collect()
    assert ref() is None, "hydrated tree must be reclaimable once dropped"
    # and a fresh access hydrates again
    assert store.geometry(idx).id == "solid1"


def test_compression_roundtrip_both_kinds():
    g = _shell_geometry()
    blob = _ngeom_blob(g)
    store = ShapeStore(compress=True)
    i_ngeom = store.add_blob(blob, gid=str(g.id))
    i_pickle = store.add_geometry(_boolean_geometry())
    assert store.record(i_ngeom).compressed and store.record(i_pickle).compressed
    assert store.nbytes < len(blob) + 100_000  # stored compressed
    assert store.ngeom_blob(i_ngeom) == blob  # decompresses to the original
    assert store.geometry(i_pickle).bool_operations[0].operator == BoolOpEnum.DIFFERENCE


def test_proxy_is_shape_and_hydrates_via_property():
    store = ShapeStore()
    g = _shell_geometry()
    idx = store.add_blob(_ngeom_blob(g), gid=str(g.id))
    p = ShapeProxy("solid1", store, idx)
    assert isinstance(p, Shape)
    assert p._geom is None, "proxy must not hold an eager tree"
    # NGEOM lowers ClosedShell -> ConnectedFaceSet; today's eager native reader
    # yields the same, so downstream behaviour is identical.
    assert isinstance(p.geom.geometry, su.ConnectedFaceSet)
    assert p.ngeom_blob() is not None

    # solid_geom() (a base-class method) works through the overridden property on
    # an accepted solid type — pickle-kind keeps the ClosedShell exact.
    idx2 = store.add_geometry(_shell_geometry("solid2"))
    p2 = ShapeProxy("solid2", store, idx2)
    solid = p2.solid_geom()
    assert solid.id == "solid2"
    assert isinstance(solid.geometry, su.ClosedShell)


def test_proxy_pin_semantics():
    store = ShapeStore()
    idx = store.add_geometry(_shell_geometry())
    p = ShapeProxy("solid1", store, idx)

    # unpinned: mutation on a transient hydration does not survive a GC cycle
    p.geom.color = "marker"
    gc.collect()
    assert p.geom.color is None

    pinned = p.pin()
    pinned.color = "marker"
    gc.collect()
    assert p.geom.color == "marker"

    # assigning .geom pins the assigned object
    other = _shell_geometry("other")
    p.geom = other
    assert p.geom is other


def test_proxy_pickle_shares_store():
    store = ShapeStore()
    g = _shell_geometry()
    blob = _ngeom_blob(g)
    idx1 = store.add_blob(blob, gid="a")
    idx2 = store.add_blob(blob, gid="b")
    p1 = ShapeProxy("a", store, idx1)
    p2 = ShapeProxy("b", store, idx2)
    r1, r2 = pickle.loads(pickle.dumps([p1, p2]))
    assert r1._shape_store is r2._shape_store, "pickle memo must keep one store"
    assert r1.geom.id == "a" and r2.geom.id == "b"
    assert isinstance(r1, ShapeProxy) and isinstance(r1, Shape)


def test_blob_fast_path_matches_serialized_tessellation():
    """A stored NGEOM blob tessellates via tessellate_stream_buffer to the same mesh
    the hydrate+re-serialize route produces (the lazy fast path skips both steps)."""
    import pytest

    from ada.cad import active_backend

    be = active_backend()
    if not hasattr(be, "tessellate_stream_buffer") or not hasattr(be, "tessellate_stream"):
        pytest.skip("active CAD backend has no NGEOM stream tessellation")

    g = _shell_geometry()
    store = ShapeStore()
    idx = store.add_blob(_ngeom_blob(g), gid=str(g.id))
    p = ShapeProxy("solid1", store, idx)

    via_blob = be.tessellate_stream_buffer(p.ngeom_blob(), pipeline="libtess2")
    hydrated = p.geom
    via_items = be.tessellate_stream([(str(hydrated.id), hydrated.geometry)], pipeline="libtess2")
    assert via_blob.positions.tobytes() == via_items.positions.tobytes()
    assert via_blob.indices.tobytes() == via_items.indices.tobytes()


def test_pickle_kind_has_no_ngeom_blob():
    store = ShapeStore()
    idx = store.add_geometry(_boolean_geometry())
    assert store.ngeom_blob(idx) is None
    p = ShapeProxy("cut1", store, idx)
    assert p.ngeom_blob() is None


def test_stream_tessellation_applies_bool_operations():
    """A Geometry wrapper's bool_operations reach the stream kernel: the serializer
    folds them into a BOOLEAN_RESULT chain (half-space lowered to a finite box, the
    same lowering adacpp's readers use) and Manifold evaluates the cut."""
    import pytest

    import ada
    from ada.cad import active_backend

    be = active_backend()
    if not hasattr(be, "tessellate_stream"):
        pytest.skip("active CAD backend has no NGEOM stream tessellation")

    box = ada.PrimBox("bx", (0, 0, 0), (1, 1, 1))
    box.add_boolean(ada.BoolHalfSpace((0.5, 0.5, 0.6), (0, 0, 1), name="cut"))
    geom = box.solid_geom()
    assert geom.bool_operations, "fixture must carry a boolean"

    bm = be.tessellate_stream([("bx", geom)], pipeline="libtess2")
    z = bm.positions.reshape(-1, 3)[:, 2] if bm.positions.ndim == 1 else bm.positions[:, 2]
    assert len(z) > 0, "boolean-bearing solid tessellated to nothing"
    zmax = float(z.max())
    assert abs(zmax - 0.6) < 1e-6, f"half-space cut not applied (zmax={zmax}, expected 0.6)"

    # solid second operand (UNION): a box fused on top raises the extent instead
    box2 = ada.PrimBox("bx2", (0, 0, 0), (1, 1, 1))
    box2.add_boolean(ada.PrimBox("cap", (0.25, 0.25, 0.5), (0.75, 0.75, 1.4)), "union")
    g2 = box2.solid_geom()
    bm2 = be.tessellate_stream([("bx2", g2)], pipeline="libtess2")
    z2 = bm2.positions.reshape(-1, 3)[:, 2] if bm2.positions.ndim == 1 else bm2.positions[:, 2]
    assert abs(float(z2.max()) - 1.4) < 1e-6, "union operand not applied"


def test_native_ifc_brep_products_import_as_ngeom_blobs(tmp_path):
    """B-rep IFC products the Python-native readers can't resolve import via adacpp's
    IfcNgeomStream as zero-copy ngeom-kind proxies instead of eager OCC kernel bodies."""
    import pytest

    import ada

    adacpp = pytest.importorskip("adacpp")
    if not hasattr(adacpp.cad, "IfcNgeomStream"):
        pytest.skip("adacpp build predates IfcNgeomStream")

    box = ada.PrimBox("bx", (0, 0, 0), (1, 1, 1))
    a = ada.Assembly("m") / (ada.Part("p") / [box])
    stp = tmp_path / "nb.stp"
    ifc = tmp_path / "nb.ifc"
    a.to_stp(stp, writer="stream")
    adacpp.cad.stream_step_to_ifc(str(stp), str(ifc))  # advanced-brep IFC4

    b = ada.from_ifc(ifc)
    shapes = [s for p in b.get_all_parts_in_assembly(include_self=True) for s in p.shapes]
    assert shapes
    for s in shapes:
        assert isinstance(s, ShapeProxy), f"{s.name}: expected lazy proxy, got {type(s).__name__}"
        assert s._occ_cache is None, f"{s.name}: eager OCC body retained"
        rec = s._shape_store.record(s._store_index)
        assert rec.kind == "ngeom", f"{s.name}: expected native blob, got {rec.kind}"
        assert s.ngeom_blob() is not None
        assert s.geom.geometry is not None  # hydrates


def test_ifc_roundtrip_imports_lazy_proxies_with_booleans(tmp_path):
    """from_ifc mints ShapeProxy objects (lazy store default-on) and a boolean cut
    (IfcBooleanClippingResult -> bool_operations) survives the store round-trip."""
    import ada

    box = ada.PrimBox("bx", (0, 0, 0), (1, 1, 1))
    box.add_boolean(ada.BoolHalfSpace((0.5, 0.5, 0.9), (0, 0, 1), name="cut"))
    a = ada.Assembly("m") / (ada.Part("p") / [box])
    f = tmp_path / "lazy_bool.ifc"
    a.to_ifc(f)

    b = ada.from_ifc(f)
    shapes = [s for p in b.get_all_parts_in_assembly(include_self=True) for s in p.shapes]
    assert shapes, "no shapes imported"
    proxies = [s for s in shapes if isinstance(s, ShapeProxy)]
    assert proxies, f"expected lazy proxies, got {[type(s).__name__ for s in shapes]}"
    geom = proxies[0].geom
    assert geom.bool_operations, "boolean clipping lost through the lazy store"
    assert geom.bool_operations[0].operator == BoolOpEnum.DIFFERENCE
