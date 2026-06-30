"""Native STEP reader (reader="native"): adacpp C++ NGEOM parse hydrated to ada.geom Geometry.

Parity oracle: ``native_stream_read_step`` must yield the SAME Geometry stream as the pure-Python
``stream_read_step`` — same products, byte-identical B-rep (serialize round-trip), colours, and the
same SET of world-placement matrices (the two readers enumerate assembly edges in a different order,
which is benign — every instance is still placed). Also exercises the end-to-end
``Part.read_step_file(reader="native")`` Assembly build.

Gated on adacpp's native stream_step_to_ngeom entry point.
"""

import numpy as np
import pytest

import ada
from ada.cadit.step.read.native_reader import (
    native_adacpp_step_available,
    native_stream_read_step,
)
from ada.cadit.step.read.stream_reader import stream_read_step

pytestmark = pytest.mark.skipif(not native_adacpp_step_available(), reason="adacpp stream_step_to_ngeom unavailable")

_AS1 = "files/step_files/as1-oc-214.stp"  # a real assembly: 5 products, placements + product names


def _transform_set(mats):
    if mats is None:
        return None
    return sorted(tuple(np.round(m.flatten(), 4)) for m in mats)


def _color(c):
    # round to absorb the native float32 vs Python float64 representation of e.g. 0.8
    return None if c is None else tuple(round(float(x), 5) for x in (c.red, c.green, c.blue))


def test_native_reader_matches_stream():
    from ada.cadit.ngeom.serialize import serialize_geometries

    nat = {g.id: g for g in native_stream_read_step(_AS1)}
    py = {g.id: g for g in stream_read_step(_AS1, local_pool=False, tolerant=True)}
    assert set(nat) == set(py) and len(py) == 5, "same products"
    for gid in py:
        gn, gp = nat[gid], py[gid]
        assert serialize_geometries([(gid, gn.geometry)]) == serialize_geometries(
            [(gid, gp.geometry)]
        ), f"{gid}: B-rep byte-identical"
        assert _color(gn.color) == _color(gp.color), f"{gid}: colour"
        # same SET of world-placement matrices (instance enumeration order may differ)
        assert _transform_set(gn.transforms) == _transform_set(gp.transforms), f"{gid}: placement set"


def test_native_reader_synthetic_box_cyl(tmp_path):
    from ada.cadit.ngeom.serialize import serialize_geometries
    from ada.visit.colors import Color

    a = ada.PrimBox("bx", (0, 0, 0), (1, 1, 1))
    a.color = Color(1, 0, 0)
    b = ada.PrimCyl("cy", (2, 0, 0), (2, 0, 1), 0.4)
    b.color = Color(0, 0, 1)
    src = tmp_path / "s.step"
    (ada.Assembly("m") / (ada.Part("p") / [a, b])).to_stp(src)

    nat = {g.id: g for g in native_stream_read_step(src)}
    assert set(nat) == {"bx", "cy"}
    for gid, geom in nat.items():
        assert geom.color is not None
        assert serialize_geometries([(gid, geom.geometry)]), "serializable B-rep"


def test_read_step_file_native_builds_assembly():
    asm = ada.Assembly("t")
    asm.read_step_file(_AS1, reader="native", product_tree=True)
    shapes = list(asm.get_all_physical_objects())
    names = {s.name for s in shapes}
    assert len(shapes) == 5, "one Shape per product"
    assert {"bolt", "nut", "plate", "rod", "l-bracket"} <= names


def test_iter_from_step_factory():
    """The public streaming factory ``ada.iter_from_step`` yields the same per-solid
    Geometry stream as the underlying readers, lazily (a generator — bounded memory)."""
    import types

    gen = ada.iter_from_step(_AS1, reader="native")
    assert isinstance(gen, types.GeneratorType)  # lazy: nothing parsed until iterated

    native = list(ada.iter_from_step(_AS1, reader="native"))
    auto = list(ada.iter_from_step(_AS1, reader="auto"))
    assert len(native) == len(auto) == 5
    assert [str(g.id) for g in auto] == [str(g.id) for g in native]
    assert all(g.geometry is not None for g in auto)

    # parity with the underlying native reader it wraps
    direct = list(native_stream_read_step(_AS1))
    assert [str(g.id) for g in native] == [str(g.id) for g in direct]
