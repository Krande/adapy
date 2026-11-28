"""Every GLB export path draws the same geometry for the same model.

adapy writes a model's GLB from many places -- ``Part.to_gltf`` in a script or notebook, the worker's
converter (``_via_ada`` / ``_export_with_ada``), the browser's conversion (``ada.cadit.wasm_convert``,
which the Pyodide worker runs), the viewer's ``.show()`` embed, the procedural compile. They all go
through :func:`ada.visit.glb_export.export_glb`; this test holds them to it on a small set of
fixtures: a doubly-curved Genie shell, IFC members (an extrusion, a CSG solid, a polygonal mesh), a
curved STEP plate, a FEM deck and a procedural document.

Each path's GLB is compared element by element (matched by name) with ``to_gltf``'s: the same
elements, the same boxes and areas, and a surface (Hausdorff) distance within the tolerance of the
leg -- identical for the legs that share the record writer and the stream kernel, a tessellation
tolerance for the CAD-backend leg.

``ADA_WASM_PARITY_DIR`` (the ``wasm-parity`` CI job) points at GLBs the same browser entry wrote under
Pyodide with the ada-cpp wasm wheel, named ``<fixture>.glb``; they are held to the same comparison.
"""

from __future__ import annotations

import json
import os
import pathlib

import numpy as np
import pytest

pytest.importorskip("adacpp")

ROOT = pathlib.Path(__file__).resolve().parents[3]
FILES = ROOT / "files"

PROCEDURAL_DOC = json.loads((FILES / "topo_model" / "two_cells_one_pump.json").read_text())

# fixture -> (wasm_convert format class, extension, path); "procedural" = a procedural document
FIXTURES = {
    "genie-curved-shell": ("genie", "xml", FILES / "fem_files" / "sesam" / "curved_shell_nurbs_strake.xml"),
    "ifc-extruded-beam": ("ifc", "ifc", FILES / "ifc_files" / "beam-extruded-solid.ifc"),
    "ifc-csg-solid": ("ifc", "ifc", FILES / "ifc_files" / "bath-csg-solid.ifc"),
    "ifc-polygonal-mesh": ("ifc", "ifc", FILES / "ifc_files" / "bs_samples" / "polygonal-face-tessellation.ifc"),
    "step-curved-plate": ("step", "stp", FILES / "step_files" / "plate_3_curved.stp"),
    "fem-deck": ("fem", "FEM", FILES / "fem_files" / "sesam" / "beamMassT1.FEM"),
    "procedural": ("procedural", "json", FILES / "topo_model" / "two_cells_one_pump.json"),
}

# The writer to_gltf must pick per fixture: the record writer wherever it can draw the model.
EXPECTED_WRITER = {name: "native" for name in FIXTURES}
EXPECTED_WRITER["fem-deck"] = "python"  # FEM elements: drawn by the scene writer (see glb_export)


def _tri_mesh_records() -> bool:
    from ada.cadit.ngeom.serialize import tri_mesh_supported

    return tri_mesh_supported()


def _adacpp_sews_pcurve_free_faces() -> bool:
    """ada-cpp builds a B-spline face bounded by 3D curves only as a valid face (the same change
    that added the TRI_MESH record query). Before it, the backend leg drew a thick curved shell flat."""
    import adacpp.cad

    return hasattr(adacpp.cad, "ngeom_mesh_record_tags")


#: Legs that share the record writer / stream kernel with to_gltf: the same triangles up to float
#: noise. The CAD backend (OCC BRepMesh) meshes the same surfaces with its own density.
EXACT = dict(hausdorff=1e-4, area=1e-4)
STEP_READERS = dict(hausdorff=2e-3, area=5e-3)  # worker/browser: adacpp's C++ STEP reader; to_gltf: OCC's
BACKEND = dict(hausdorff=0.06, area=0.02, rel_hausdorff=0.02)
#: The same code on the ada-cpp wasm build: float evaluation may differ in the last bits.
WASM = dict(hausdorff=2e-3, area=5e-3)


def _load(fixture: str):
    import ada

    fmt, ext, path = FIXTURES[fixture]
    if fmt == "procedural":
        from ada.topo_model import ProceduralBuilder

        pb = ProceduralBuilder.from_dict(PROCEDURAL_DOC)
        pb.compile()
        return pb.assembly
    if fmt == "genie":
        return ada.from_genie_xml(path)
    if fmt == "ifc":
        return ada.from_ifc(path)
    if fmt == "step":
        return ada.from_step(path)
    if fmt == "fem":
        return ada.from_fem(path)  # as the worker and the browser read a deck for a GLB
    raise AssertionError(fmt)


def _to_gltf(model, **kw) -> bytes:
    import io

    buf = io.BytesIO()
    model.to_gltf(buf, **kw)
    return buf.getvalue()


def _worker(fixture: str) -> bytes:
    from ada.comms.rest.converters.ada_export import _via_ada

    fmt, ext, path = FIXTURES[fixture]
    res = _via_ada(path, f".{ext.lower()}", "glb", lambda *_a, **_k: None)
    if isinstance(res, (bytes, bytearray)):
        return bytes(res)
    p = pathlib.Path(res)
    try:
        return p.read_bytes()
    finally:
        p.unlink(missing_ok=True)


def _browser(fixture: str) -> bytes:
    from ada.cadit import wasm_convert

    fmt, ext, path = FIXTURES[fixture]
    return bytes(wasm_convert.run(fmt, ext, "glb", str(path)))


def _viewer(model) -> bytes:
    """What ``.show()`` embeds (renderer_manager): the record writer's GLB, else the scene writer's."""
    from ada.visit.glb_export import export_env, native_glb_bytes
    from ada.visit.render_params import RenderParams
    from ada.visit.scene_converter import SceneConverter

    params = RenderParams()
    with export_env(model):  # as RendererManager.render wraps it
        return native_glb_bytes(model, params) or SceneConverter(model, params).build_glb()


def _meshes(glb: bytes, tmp_path: pathlib.Path, label: str) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    from ada.geom_compare import glb as g
    from ada.visit.gltf.meshopt import meshopt_decompress_glb

    n = int.from_bytes(glb[12:16], "little")
    if b"EXT_meshopt_compression" in glb[20 : 20 + n]:
        src, dst = tmp_path / f"{label}.glb", tmp_path / f"{label}.plain.glb"
        src.write_bytes(glb)
        meshopt_decompress_glb(src, dst)
        glb = dst.read_bytes()
    js, binary = g.parse_glb(glb)
    hierarchy = g.merged_hierarchy(js)
    assert hierarchy is not None, f"{label}: the GLB carries no id_hierarchy"
    names = sorted({el.name for el in g.elements(js, binary, hierarchy).values()})
    out = {}
    for name, m in g.element_meshes(js, binary, hierarchy, names).items():
        pos = np.asarray(m.positions, dtype=float).reshape(-1, 3)
        tri = np.asarray(m.indices, dtype=np.int64).reshape(-1, 3)
        if len(tri):
            out[name] = (pos, tri)
    return out


def _area(pos, tri) -> float:
    a, b, c = pos[tri[:, 0]], pos[tri[:, 1]], pos[tri[:, 2]]
    return float(0.5 * np.linalg.norm(np.cross(b - a, c - a), axis=1).sum())


def _assert_same_geometry(ref, got, tol, label):
    from ada.geom_compare.distance import surface_distance

    assert set(got) == set(ref), f"{label}: elements differ: missing {set(ref) - set(got)}, extra {set(got) - set(ref)}"
    for name, (rp, rt) in ref.items():
        gp, gt = got[name]
        diag = float(np.linalg.norm(rp.max(0) - rp.min(0)))
        h_tol = max(tol["hausdorff"], tol.get("rel_hausdorff", 0.0) * diag)
        assert np.abs(gp.min(0) - rp.min(0)).max() <= h_tol, f"{label}/{name}: box min {gp.min(0)} vs {rp.min(0)}"
        assert np.abs(gp.max(0) - rp.max(0)).max() <= h_tol, f"{label}/{name}: box max {gp.max(0)} vs {rp.max(0)}"
        assert _area(gp, gt) == pytest.approx(_area(rp, rt), rel=tol["area"]), f"{label}/{name}: area"
        d = surface_distance(gp.ravel(), gt.ravel(), rp.ravel(), rt.ravel())
        assert d.hausdorff <= h_tol, f"{label}/{name}: surface distance {d.hausdorff:.5f} m > {h_tol:.5f}"


@pytest.fixture(scope="module")
def reference(tmp_path_factory):
    """``to_gltf``'s GLB per fixture, and the writer it went through."""
    from ada.visit.glb_export import last_report

    tmp = tmp_path_factory.mktemp("ref")
    out = {}
    for name in FIXTURES:
        model = _load(name)
        glb = _to_gltf(model)
        out[name] = (model, _meshes(glb, tmp, name), last_report())
    return out


@pytest.mark.parametrize("fixture", list(FIXTURES))
def test_to_gltf_takes_the_record_writer(reference, fixture):
    _model, meshes, report = reference[fixture]
    assert meshes, f"{fixture}: to_gltf drew nothing"
    expected = EXPECTED_WRITER[fixture]
    if fixture == "ifc-polygonal-mesh" and not _tri_mesh_records():
        expected = "python"  # a mesh body needs ada-cpp's TRI_MESH record
    assert report.writer == expected, f"{fixture}: {report}"


@pytest.mark.parametrize("fixture", [f for f in FIXTURES if f != "procedural"])
def test_worker_matches_to_gltf(reference, fixture, tmp_path):
    tol = STEP_READERS if fixture.startswith("step") else EXACT
    _assert_same_geometry(reference[fixture][1], _meshes(_worker(fixture), tmp_path, "worker"), tol, "worker")


@pytest.mark.parametrize("fixture", [f for f in FIXTURES if f != "procedural"])
def test_browser_entry_matches_to_gltf(reference, fixture, tmp_path):
    tol = STEP_READERS if fixture.startswith("step") else EXACT
    _assert_same_geometry(reference[fixture][1], _meshes(_browser(fixture), tmp_path, "browser"), tol, "browser")


@pytest.mark.parametrize("fixture", list(FIXTURES))
def test_viewer_embed_matches_to_gltf(reference, fixture, tmp_path):
    model, ref, _ = reference[fixture]
    _assert_same_geometry(ref, _meshes(_viewer(model), tmp_path, "viewer"), EXACT, "viewer")


def test_procedural_compile_matches_to_gltf(reference, tmp_path):
    from ada.topo_model import ProceduralBuilder

    glb = ProceduralBuilder.from_dict(PROCEDURAL_DOC).compile()
    _assert_same_geometry(reference["procedural"][1], _meshes(glb, tmp_path, "compile"), EXACT, "compile")


@pytest.mark.parametrize("fixture", list(FIXTURES))
def test_scene_writer_stream_leg_matches(reference, fixture, tmp_path):
    """The scene writer with the stream kernel -- the leg the record writer falls back to."""
    model, ref, _ = reference[fixture]
    got = _meshes(_to_gltf(model, writer="python"), tmp_path, "scene")
    _assert_same_geometry(ref, got, EXACT, "scene-stream")


@pytest.mark.parametrize("fixture", list(FIXTURES))
def test_scene_writer_backend_leg_matches(reference, fixture, tmp_path):
    """The CAD backend's own meshing (the last fallback): the same surfaces, its own density."""
    if fixture == "genie-curved-shell" and not _adacpp_sews_pcurve_free_faces():
        pytest.xfail("this ada-cpp sews a thick curved shell's pcurve-free side walls away")
    model, ref, _ = reference[fixture]
    got = _meshes(_to_gltf(model, writer="python", glb_tess_engine="occ-builtin"), tmp_path, "backend")
    _assert_same_geometry(ref, got, BACKEND, "scene-backend")


@pytest.mark.skipif(not os.environ.get("ADA_WASM_PARITY_DIR"), reason="no wasm-produced GLBs (ADA_WASM_PARITY_DIR)")
@pytest.mark.parametrize("fixture", list(FIXTURES))
def test_pyodide_glb_matches_to_gltf(reference, fixture, tmp_path):
    """The GLB Pyodide + the ada-cpp wasm wheel wrote through the same browser entry
    (``wasm_convert.run``; the procedural document through ``wasm_compile.compile_doc``)."""
    path = pathlib.Path(os.environ["ADA_WASM_PARITY_DIR"]) / f"{fixture}.glb"
    if not path.exists():
        skipped = path.with_suffix(".skipped")
        pytest.skip(skipped.read_text() if skipped.exists() else f"the wasm run wrote no {path.name}")
    tol = STEP_READERS if fixture.startswith("step") else WASM
    _assert_same_geometry(reference[fixture][1], _meshes(path.read_bytes(), tmp_path, "wasm"), tol, "pyodide")
