"""A Genie ``<curved_shell>`` reaches the GLB as its curved surface, in the browser as on the worker.

The fixture is one doubly-curved NURBS strake (rational quadratic one way, cubic the other, 25 mm
thick) referenced from an embedded SAT body. The browser's conversion (``ada.cadit.wasm_convert``,
which the Pyodide worker runs) used to call a bare ``Assembly.to_gltf()``. That picks neither the
worker's tessellation engine nor its native record writer, and sent the plate through the backend's
face sewing, which kept one face of the shell's six; the area guard then swapped in a flat
best-fit-plane fallback that stands up to ~0.23 m off the real surface. The worker drew it curved.

* the native tests pin what the plate must look like, and that the browser's entry agrees with the
  worker's converter when both run on the native ada-cpp;
* ``test_wasm_glb_matches_the_worker`` compares a GLB the same browser entry produced under Pyodide
  with the ada-cpp wasm wheel (``scripts/testing/pyodide-test/test_pyodide_genie_curved_shell.js``
  writes it; the CI job ``wasm-parity`` points ``ADA_WASM_GENIE_GLB`` at it) with the worker's.
"""

from __future__ import annotations

import os
import pathlib

import pytest

pytest.importorskip("adacpp")

ROOT = pathlib.Path(__file__).resolve().parents[3]
FIXTURE = ROOT / "files" / "fem_files" / "sesam" / "curved_shell_nurbs_strake.xml"
PLATE = "curved_plate_1"
THICKNESS = 0.025

# The SAT face's own box (its control net's extremes are on the boundary): the curved plate must
# cover it, and may exceed it by no more than the thickness it is offset by.
FACE_LO = (-60.7, 32.339392013277816, 5.97487373415292)
FACE_HI = (-56.7, 34.5, 7.0000000000000036)
# Two curved faces of ~6.22 m^2 each plus the thin side walls.
AREA = 12.67


def _native_writers_or_skip():
    from ada.cadit.ngeom.export import native_mesh_writers_available

    if not native_mesh_writers_available():
        pytest.skip("this ada-cpp has no NGEOM-record mesh writer")


def _plain(glb: bytes, tmp_path: pathlib.Path, label: str) -> bytes:
    """``glb`` without EXT_meshopt_compression (the worker's GLB carries it)."""
    from ada.visit.gltf.meshopt import meshopt_decompress_glb

    src = tmp_path / f"{label}.glb"
    src.write_bytes(glb)
    if b"EXT_meshopt_compression" not in glb[: 20 + int.from_bytes(glb[12:16], "little")]:
        return glb
    dst = tmp_path / f"{label}.plain.glb"
    meshopt_decompress_glb(src, dst)
    return dst.read_bytes()


def _plate_mesh(glb: bytes, tmp_path: pathlib.Path, label: str):
    import numpy as np

    from ada.geom_compare import glb as g

    js, binary = g.parse_glb(_plain(glb, tmp_path, label))
    hierarchy = g.merged_hierarchy(js)
    assert hierarchy is not None, "the GLB carries no id_hierarchy"
    meshes = g.element_meshes(js, binary, hierarchy, [PLATE])
    assert PLATE in meshes, f"{PLATE} is not in the GLB"
    m = meshes[PLATE]
    return np.asarray(m.positions, dtype=float).reshape(-1, 3), np.asarray(m.indices, dtype=np.int64).reshape(-1, 3)


def _area(pos, tri) -> float:
    import numpy as np

    a, b, c = pos[tri[:, 0]], pos[tri[:, 1]], pos[tri[:, 2]]
    return float(0.5 * np.linalg.norm(np.cross(b - a, c - a), axis=1).sum())


def _browser_glb() -> bytes:
    from ada.cadit import wasm_convert

    return bytes(wasm_convert.run("genie", "xml", "glb", str(FIXTURE)))


def _worker_glb() -> bytes:
    from ada.comms.rest.converters.ada_export import _via_ada

    res = _via_ada(FIXTURE, ".xml", "glb", lambda *_a, **_k: None)
    if isinstance(res, (bytes, bytearray)):
        return bytes(res)
    path = pathlib.Path(res)
    try:
        return path.read_bytes()
    finally:
        path.unlink(missing_ok=True)


def _assert_follows_the_surface(pos, tri):
    import numpy as np

    lo, hi = pos.min(axis=0), pos.max(axis=0)
    tol = 1e-3
    # Covers the curved face: a flat fallback misses its low corner by ~0.23 m in z.
    assert np.all(lo <= np.asarray(FACE_LO) + tol), f"plate box {lo} does not reach the face's {FACE_LO}"
    assert np.all(hi >= np.asarray(FACE_HI) - tol), f"plate box {hi} does not reach the face's {FACE_HI}"
    assert np.all(lo >= np.asarray(FACE_LO) - THICKNESS - tol), f"plate box {lo} overshoots the face"
    assert np.all(hi <= np.asarray(FACE_HI) + THICKNESS + tol), f"plate box {hi} overshoots the face"
    assert _area(pos, tri) == pytest.approx(AREA, rel=0.01)


def test_browser_entry_draws_the_curved_shell(tmp_path):
    """The browser's conversion entry, run natively: the plate is the curved surface, thickened."""
    _native_writers_or_skip()
    _assert_follows_the_surface(*_plate_mesh(_browser_glb(), tmp_path, "browser"))


def test_worker_draws_the_curved_shell(tmp_path):
    _native_writers_or_skip()
    _assert_follows_the_surface(*_plate_mesh(_worker_glb(), tmp_path, "worker"))


def _assert_parity(browser, worker):
    import numpy as np

    from ada.geom_compare.distance import surface_distance

    (bp, bt), (wp, wt) = browser, worker
    assert np.abs(bp.min(0) - wp.min(0)).max() < 1e-3
    assert np.abs(bp.max(0) - wp.max(0)).max() < 1e-3
    assert _area(bp, bt) == pytest.approx(_area(wp, wt), rel=5e-3)
    d = surface_distance(bp.ravel(), bt.ravel(), wp.ravel(), wt.ravel())
    assert d.hausdorff < 2e-3, f"browser vs worker surface distance {d}"


def test_browser_entry_matches_the_worker(tmp_path):
    """Both natively: the browser's entry and the worker's converter give the same plate."""
    _native_writers_or_skip()
    _assert_parity(_plate_mesh(_browser_glb(), tmp_path, "browser"), _plate_mesh(_worker_glb(), tmp_path, "worker"))


@pytest.mark.skipif(not os.environ.get("ADA_WASM_GENIE_GLB"), reason="no wasm-produced GLB (ADA_WASM_GENIE_GLB)")
def test_wasm_glb_matches_the_worker(tmp_path):
    """The GLB Pyodide + the ada-cpp wasm wheel produced for the fixture, against the worker's."""
    _native_writers_or_skip()
    wasm = pathlib.Path(os.environ["ADA_WASM_GENIE_GLB"]).read_bytes()
    browser = _plate_mesh(wasm, tmp_path, "wasm")
    _assert_follows_the_surface(*browser)
    _assert_parity(browser, _plate_mesh(_worker_glb(), tmp_path, "worker"))
