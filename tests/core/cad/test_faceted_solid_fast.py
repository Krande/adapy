"""A FacetedBrep built straight from its point loops (ada-cpp's ``solid_from_polygons``), many at
once (``build_solids`` / ``prebuild_solids``), and the face-by-face route an older ada-cpp takes.

All geometry is synthetic: cubes, and a triangulated bead-like loft (a fillet-shaped section swept
round an arc).
"""

import math

import pytest

import ada
from ada.geom import Geometry
from ada.geom import curves as geo_cu
from ada.geom import solids as geo_so
from ada.geom import surfaces as geo_su
from ada.geom.points import Point

adacpp_cad = pytest.importorskip("adacpp.cad")

needs_polygon_solids = pytest.mark.skipif(
    not hasattr(adacpp_cad, "solid_from_polygons"), reason="this ada-cpp has no solid_from_polygons"
)
needs_polygon_batch = pytest.mark.skipif(
    not hasattr(adacpp_cad, "solids_from_polygons_batch"), reason="this ada-cpp has no solids_from_polygons_batch"
)


def _shell(loops) -> geo_su.ClosedShell:
    return geo_su.ClosedShell(
        cfs_faces=[
            geo_su.Face(
                bounds=[geo_su.FaceBound(bound=geo_cu.PolyLoop(polygon=[Point(*p) for p in loop]), orientation=True)]
            )
            for loop in loops
        ]
    )


def _cube_loops(origin=(0.0, 0.0, 0.0), size=1.0):
    ox, oy, oz = origin
    p = [(ox + x * size, oy + y * size, oz + z * size) for x in (0, 1) for y in (0, 1) for z in (0, 1)]
    quads = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
    return [[p[i] for i in q] for q in quads]


def _bead_loops(n_stations=26, leg=8.0, radius=200.0, sweep=math.radians(40), x0=0.0):
    sec = [(0.0, 0.0), (leg, 0.0), (0.6 * leg, 0.6 * leg), (0.0, leg)]
    rings = []
    for i in range(n_stations):
        th = sweep * i / (n_stations - 1)
        c, s = math.cos(th), math.sin(th)
        rings.append([(x0 + (radius + u) * c, (radius + u) * s, v) for u, v in sec])
    loops = []
    for a, b in zip(rings, rings[1:]):
        for k in range(len(sec)):
            k1 = (k + 1) % len(sec)
            loops += [[a[k], b[k], b[k1]], [a[k], b[k1], a[k1]]]
    return loops + [list(reversed(rings[0])), rings[-1]]


def _faceted(name, loops, voids=()):
    fb = geo_so.FacetedBrep(outer=_shell(loops), voids=[_shell(v) for v in voids])
    return ada.Shape(name, geom=Geometry(name, fb, None))


@pytest.fixture
def backend():
    from ada.cad import AdacppBackend

    return AdacppBackend()


@pytest.fixture
def legacy(backend, monkeypatch):
    """The same backend as if its ada-cpp had no solid_from_polygons."""
    monkeypatch.setattr(type(backend), "_has_polygon_solids", lambda self: False)
    return backend


def test_faceted_loops_take_only_plain_polygons(backend):
    shell = _shell(_cube_loops())
    loops = backend._faceted_loops(shell)
    assert len(loops) == 6 and all(len(lp) == 4 and len(lp[0]) == 3 for lp in loops)
    holed = _shell(_cube_loops())
    holed.cfs_faces[0].bounds.append(holed.cfs_faces[1].bounds[0])  # a face with an inner bound
    assert backend._faceted_loops(holed) is None


@needs_polygon_solids
@pytest.mark.parametrize("reversed_faces", [False, True])
def test_bead_matches_the_face_by_face_route(backend, reversed_faces):
    loops = _bead_loops()
    if reversed_faces:
        loops = [list(reversed(lp)) for lp in loops]
    geom = _faceted("bead", loops).solid_geom()
    fast = backend.build(geom)
    assert backend.is_valid(fast)
    ref = backend._solid_from_faces(backend._shell_faces(geom.id, geom.geometry.outer))
    assert backend.volume(fast) > 0
    assert backend.volume(fast) == pytest.approx(backend.volume(ref), rel=1e-9)


@needs_polygon_solids
def test_voids_and_booleans_on_the_fast_path(backend):
    hollow = _faceted("hollow", _cube_loops(size=2.0), voids=[_cube_loops(origin=(0.5, 0.5, 0.5))])
    assert backend.volume(backend.build(hollow.solid_geom())) == pytest.approx(7.0, rel=1e-9)
    cube = _faceted("cube", _cube_loops())
    cube.add_boolean(ada.PrimCyl("bore", (0.5, 0.5, -1.0), (0.5, 0.5, 2.0), 0.25))
    assert backend.volume(backend.build(cube.solid_geom())) == pytest.approx(1.0 - math.pi / 16, rel=1e-6)


def test_legacy_route_still_builds(legacy):
    geom = _faceted("bead", _bead_loops(n_stations=6)).solid_geom()
    assert legacy.volume(legacy.build(geom)) > 0
    hollow = _faceted("hollow", _cube_loops(size=2.0), voids=[_cube_loops(origin=(0.5, 0.5, 0.5))])
    assert legacy.volume(legacy.build(hollow.solid_geom())) == pytest.approx(7.0, rel=1e-9)


def test_open_shell_raises_value_error(backend):
    with pytest.raises(ValueError):
        backend.build(_faceted("open", _cube_loops()[:-1]).solid_geom())


def _mixed_geometries():
    beads = [_faceted(f"b{i}", _bead_loops(x0=40.0 * i)) for i in range(5)]
    hollow = _faceted("hollow", _cube_loops(size=2.0), voids=[_cube_loops(origin=(0.5, 0.5, 0.5))])
    box = ada.PrimBox("box", (0, 0, 0), (1, 2, 3))
    bored = _faceted("bored", _cube_loops())
    bored.add_boolean(ada.PrimCyl("bore", (0.5, 0.5, -1.0), (0.5, 0.5, 2.0), 0.25))
    broken = _faceted("open", _cube_loops()[:-1])
    objs = beads + [hollow, box, bored, broken]
    return objs, [o.solid_geom() for o in objs]


def test_build_many_matches_build_and_isolates_failures(backend):
    objs, geoms = _mixed_geometries()
    shapes, errors = backend.build_many(geoms, threads=2)
    assert len(shapes) == len(errors) == len(geoms)
    assert shapes[-1] is None and errors[-1].startswith("ValueError")
    for g, s, e in zip(geoms[:-1], shapes[:-1], errors[:-1]):
        assert e is None
        assert backend.volume(s) == pytest.approx(backend.volume(backend.build(g)), rel=1e-9)


@needs_polygon_batch
def test_build_many_batches_the_faceted_ones(backend, monkeypatch):
    calls = []
    real = backend._cad.solids_from_polygons_batch

    class _Spy:
        def __getattr__(self, name):
            return getattr(adacpp_cad, name)

        def solids_from_polygons_batch(self, items, **kw):
            calls.append(len(items))
            return real(items, **kw)

    monkeypatch.setattr(backend, "_cad", _Spy())
    _, geoms = _mixed_geometries()
    shapes, errors = backend.build_many(geoms)
    # 5 beads + hollow (outer + void) + bored + open = 9 shells in one batch; the box is not faceted.
    assert calls == [9]
    assert errors[:-1] == [None] * (len(geoms) - 1)


def test_build_solids_without_build_many(backend):
    from ada.cad import build_solids

    class _OneByOne:
        name = "one-by-one"

        def build(self, geometry):
            return backend.build(geometry)

    _, geoms = _mixed_geometries()
    shapes, errors = build_solids(geoms, backend=_OneByOne())
    assert errors[:-1] == [None] * (len(geoms) - 1) and errors[-1].startswith("ValueError")
    assert backend.volume(shapes[0]) > 0


def test_prebuild_solids_fills_the_shape_cache():
    from ada.cad import shape_cache

    objs, _ = _mixed_geometries()
    shape_cache.clear_all()
    try:
        errors = shape_cache.prebuild_solids(objs, threads=2)
        assert errors[:-1] == [None] * (len(objs) - 1)
        assert errors[-1] is not None and "no volume" in errors[-1]
        for obj in objs[:-1]:
            key = shape_cache._key(obj)
            assert key in shape_cache.occ_solid_cache
            assert shape_cache.get_solid_occ(obj) is shape_cache.occ_solid_cache[key]
        assert shape_cache._key(objs[-1]) not in shape_cache.occ_solid_cache
        # Already cached: nothing rebuilt, nothing reported.
        before = dict(shape_cache.occ_solid_cache)
        assert shape_cache.prebuild_solids(objs[:-1]) == [None] * (len(objs) - 1)
        assert all(shape_cache.occ_solid_cache[k] is v for k, v in before.items())
    finally:
        shape_cache.clear_all()
