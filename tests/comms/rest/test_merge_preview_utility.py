"""The ``merge-preview`` worker utility: algorithm-swappable FEM plate-merge preview.

Covers registration + advertised spec, the analyze partition (coplanar vs none),
the end-to-end handler (uploads a colorized overlay GLB + returns stats), and the
clean errors for an unimplemented algorithm and a non-FEM source.
"""

from __future__ import annotations

import pytest

import ada
from ada import Node
from ada.comms.rest import utilities  # noqa: F401  registers the utilities
from ada.comms.rest.utility import run_utility, UtilityRegistry
from ada.fem.formats.merge_preview import analyze_part


def _shell(el_id, nodes, fem):
    for n in nodes:
        fem.nodes.add(n)
    el = fem.add_elem(ada.fem.Elem(el_id, nodes, ada.fem.Elem.EL_TYPES.SHELL_SHAPES.QUAD, el_formulation_override="S4"))
    fs = fem.add_section(
        ada.fem.FemSection(f"PlSec{el_id}", "shell", ada.fem.FemSet("S", [el]), ada.Material("S355"), thickness=10e-3)
    )
    el.fem_sec = fs


def _two_coplanar_quads() -> "ada.Assembly":
    """Two planar (z=0) quads sharing one edge → one coplanar edge-connected region."""
    p = ada.Part("p")
    _shell(1, [Node([0, 0, 0], 1), Node([1, 0, 0], 2), Node([1, 1, 0], 3), Node([0, 1, 0], 4)], p.fem)
    _shell(2, [Node([1, 0, 0], 2), Node([2, 0, 0], 5), Node([2, 1, 0], 6), Node([1, 1, 0], 3)], p.fem)
    a = ada.Assembly() / p
    p.fem.sections.merge_by_properties()
    return a


class _FakeStore:
    def __init__(self):
        self.blobs: dict = {}

    def put_bytes(self, key, data, content_encoding=None):
        self.blobs[key] = data


def test_registered_with_algorithm_swap_spec():
    assert "merge-preview" in UtilityRegistry.names()
    spec = next(s for s in UtilityRegistry.specs() if s["name"] == "merge-preview")
    kw = {k["name"]: k for k in spec["kwargs"]}
    assert {"action", "algorithm", "mode", "ndigits", "angle_tol", "min_patch_quads"} <= set(kw)
    assert set(kw["algorithm"]["enum"]) == {"auto", "none", "coplanar", "planar", "surface", "classify", "panel"}
    assert set(kw["action"]["enum"]) == {"preview", "generate"}
    assert set(kw["mode"]["enum"]) == {"status", "achieved", "component", "class"}


def test_analyze_coplanar_merges_none_does_not():
    a = _two_coplanar_quads()
    none = analyze_part(a, "none").stats
    cop = analyze_part(a, "coplanar").stats
    assert none["primitives"] == 2 and none["achieved_plates"] == 2  # raw baseline: no merge
    assert cop["achieved_plates"] == 1  # the two coplanar edge-sharing quads collapse
    assert cop["reduction_actual"] == 2.0
    assert cop["strategy"] == "coplanar" and none["strategy"] == "none"


def test_end_to_end_uploads_overlay_and_reports_stats(monkeypatch, tmp_path):
    a = _two_coplanar_quads()
    monkeypatch.setattr(ada, "from_fem", lambda *_a, **_k: a)
    src = tmp_path / "m.fem"
    src.write_text("stub")  # suffix is what the handler checks; content unused (from_fem patched)
    store = _FakeStore()

    payload = run_utility(
        "merge-preview",
        str(src),
        storage=store,
        scope=None,
        on_progress=lambda *_: None,
        kwargs={"algorithm": "coplanar", "mode": "status"},
    )

    overlay = [o for o in payload["ops"] if o["op"] == "add_overlay_geometry"]
    assert overlay, "expected an add_overlay_geometry op"
    key = overlay[0]["blob_key"]
    assert key in store.blobs and store.blobs[key][:4] == b"glTF"  # a real GLB was uploaded
    assert payload["summary"]["achieved_plates"] == 1
    assert payload["version"] == 1  # run_utility stamps the viewops version


def test_surface_region_grows_smooth_patch():
    a = _two_coplanar_quads()  # two edge-adjacent quads, same normal -> one smooth patch
    s = analyze_part(a, "surface", min_patch_quads=2).stats
    assert s["strategy"] == "surface"
    assert s["surface_patches"] == 1  # grown into a single fitted-surface patch
    assert s["achieved_plates"] == 1
    assert s["angle_tol"] == 30.0


def test_planar_strategy_partition_and_writer_agree():
    # two coplanar edge-sharing quads: planar growing collapses them to one flat plate,
    # and the object-free writer emits the same single face.
    from ada.fem.formats.mesh_faces import MergeStrategy, iter_faces

    a = _two_coplanar_quads()
    s = analyze_part(a, "planar").stats
    assert s["strategy"] == "planar"
    assert s["achieved_plates"] == 1

    faces = list(iter_faces(a, MergeStrategy.PLANAR))
    assert len(faces) == 1  # writer emits one flat face for the flat patch


def test_auto_strategy_mirrors_production_classes():
    # the "auto" preview mirrors the production analytic emit: the two coplanar quads are a
    # planar-merged group (not curved/facet), matching iter_fem_analytic_faces.
    a = _two_coplanar_quads()
    s = analyze_part(a, "auto", min_patch_quads=2).stats
    assert s["strategy"] == "auto"
    assert s["patches_by_class"].get("planar") == 1
    assert s["patches_by_class"].get("curved") is None  # flat quads are not reconstructed as curved
    assert s["achieved_plates"] == 1


@pytest.mark.adacpp_stream
def test_generate_action_builds_plate_glb(monkeypatch, tmp_path):
    # action="generate" builds real merged Plate objects and uploads a viewable GLB. Under adacpp
    # this drives the NGEOM streaming tessellator (tessellate_stream); under OCC the object-path
    # fallback. The adacpp_stream marker lets CI run it against the native backend (test-rest-adacpp).
    a = _two_coplanar_quads()
    monkeypatch.setattr(ada, "from_fem", lambda *_a, **_k: a)
    src = tmp_path / "m.fem"
    src.write_text("stub")
    store = _FakeStore()

    payload = run_utility(
        "merge-preview",
        str(src),
        storage=store,
        scope=None,
        on_progress=lambda *_: None,
        kwargs={"action": "generate", "algorithm": "auto"},
    )
    overlay = [o for o in payload["ops"] if o["op"] == "add_overlay_geometry"]
    assert overlay and overlay[0]["blob_key"] in store.blobs
    assert store.blobs[overlay[0]["blob_key"]][:4] == b"glTF"  # a real GLB (libtess2-tessellated)
    assert payload["summary"]["action"] == "generate"
    assert payload["summary"]["faces"] == 1  # two coplanar quads → one merged planar face
    assert payload["summary"]["planar_faces"] == 1
    # overlay is named by the model, not the temp file (so the utils menu can scope it)
    assert overlay[0]["blob_key"].startswith("_overlays/")
    # per-plate picking contract: id_hierarchy + draw_ranges_node* in scene.extras + ADA_EXT_data
    import json
    import struct

    glb = store.blobs[overlay[0]["blob_key"]]
    jlen = struct.unpack("<I", glb[12:16])[0]
    gltf = json.loads(glb[20 : 20 + jlen])
    extras = gltf["scenes"][0]["extras"]
    assert "id_hierarchy" in extras and len(extras["id_hierarchy"]) >= 2  # root + plate(s)
    dr = [k for k in extras if k.startswith("draw_ranges_node")]
    assert dr and sum(len(extras[k]) for k in dr) == 1  # one draw range for the single plate
    for k in dr:
        for _nid, rng in extras[k].items():
            assert len(rng) == 2 and rng[1] > 0  # [start, count]
    ada_ext = gltf.get("extensions", {}).get("ADA_EXT_data")
    assert ada_ext is not None
    assert isinstance(ada_ext.get("design_objects"), list) and len(ada_ext["design_objects"]) == 1
    assert isinstance(ada_ext.get("simulation_objects"), list)


def test_classify_recognizes_planar_patch():
    a = _two_coplanar_quads()  # one flat smooth patch
    s = analyze_part(a, "classify", min_patch_quads=2).stats
    assert s["strategy"] == "classify"
    assert s["patches_by_class"].get("planar") == 1
    assert s["achieved_plates"] == 1


def test_fit_cylinder_and_plane_math():
    import numpy as np

    from ada.fem.formats.mesh_faces import _fit_cylinder, _fit_plane

    # synthetic cylinder about +z, r=2, radial normals
    th = np.linspace(0, 2 * np.pi, 40, endpoint=False)
    z = np.linspace(0, 5, 10)
    T, Z = np.meshgrid(th, z)
    T, Z = T.ravel(), Z.ravel()
    pts = np.column_stack([2.0 * np.cos(T), 2.0 * np.sin(T), Z])
    normals = np.column_stack([np.cos(T), np.sin(T), np.zeros_like(T)])
    r, rel, span_over_r = _fit_cylinder(pts, normals)
    assert abs(r - 2.0) < 1e-6
    assert rel < 1e-6  # exact cylinder
    assert span_over_r > 1.0

    _n, dev = _fit_plane(np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0.0]]))
    assert dev < 1e-9  # exact plane


def _synthetic_cylinder_prims(r=2.0, n_theta=40, n_z=8, length=5.0):
    """A closed cylindrical quad mesh as a _Primitives (radial normals), for fit tests."""
    import numpy as np

    from ada.fem.formats.mesh_faces import _Primitives

    th = np.linspace(0, 2 * np.pi, n_theta, endpoint=False)
    zs = np.linspace(0, length, n_z)
    coords = np.array([[r * np.cos(t), r * np.sin(t), z] for z in zs for t in th])

    def idx(i, j):
        return i * n_theta + (j % n_theta)

    rows, normals = [], []
    for i in range(n_z - 1):
        for j in range(n_theta):
            rows.append((idx(i, j), idx(i, j + 1), idx(i + 1, j + 1), idx(i + 1, j)))
            tc = th[j] + (th[1] - th[0]) / 2
            normals.append([np.cos(tc), np.sin(tc), 0.0])
    prims = _Primitives(
        coords, rows, [f"q{i}" for i in range(len(rows))], ["m"] * len(rows), [0.01] * len(rows), np.array(normals)
    )
    return prims, list(range(len(rows)))


def test_fit_cylinder_params_full_tube():
    from ada.fem.formats.mesh_faces import fit_cylinder_params

    prims, patch = _synthetic_cylinder_prims(r=2.0, length=5.0)
    cf = fit_cylinder_params(prims, patch)
    assert cf is not None
    assert abs(cf.radius - 2.0) < 1e-3
    assert cf.full360 is True  # closed tube
    assert abs((cf.z1 - cf.z0) - 5.0) < 1e-3  # axial extent recovered
    assert cf.max_rel_resid < 0.02


def test_fit_cylinder_params_partial_arc():
    from ada.fem.formats.mesh_faces import fit_cylinder_params

    prims, patch = _synthetic_cylinder_prims(r=2.0, n_theta=40)
    # keep only columns 0..19 of the circumference (row index j: column = j % n_theta)
    half = [j for j in patch if j % 40 < 20]
    cf = fit_cylinder_params(prims, half)
    assert cf is not None and cf.full360 is False
    assert 0.3 < (cf.theta_max - cf.theta_min) < 6.0  # a genuine arc, not the full circle


def test_cylinder_fit_to_faces_emits_cylindrical_surface():
    """A fitted tube -> analytic ada.geom cylinder faces that the STEP writer emits as
    CYLINDRICAL_SURFACE (backend-independent: exercises the ap242 emit, not the CAD
    build)."""
    import os
    import tempfile

    from ada.cadit.step.write.ap242_stream import Ap242StreamWriter
    from ada.fem.formats.mesh_faces import cylinder_fit_to_faces, fit_cylinder_params
    from ada.geom.surfaces import AdvancedFace, CylindricalSurface, OpenShell, ShellBasedSurfaceModel

    prims, patch = _synthetic_cylinder_prims(r=2.0, length=5.0)
    cf = fit_cylinder_params(prims, patch)
    faces = cylinder_fit_to_faces(cf)
    assert 1 <= len(faces) <= 4  # a full tube splits into a few <=180-deg arc segments
    assert all(isinstance(f, AdvancedFace) and isinstance(f.face_surface, CylindricalSurface) for f in faces)
    assert all(abs(f.face_surface.radius - 2.0) < 1e-3 for f in faces)

    shell = ShellBasedSurfaceModel(sbsm_boundary=[OpenShell(cfs_faces=faces)])
    out = tempfile.mktemp(suffix=".step")
    try:
        with open(out, "w") as fh:
            w = Ap242StreamWriter(fh, schema="AP242", assembly=True)
            with w:
                assert w.add_solid_instances(shell, name="cyl", color=None, instances=[(None, None)]) == 1
        with open(out) as fh:
            txt = fh.read()
        assert txt.count("CYLINDRICAL_SURFACE") == len(faces)  # one analytic cylinder face per segment
    finally:
        if os.path.exists(out):
            os.unlink(out)


def test_iter_fem_analytic_faces_assembles_faces():
    """The analytic emit source yields ada.geom faces per FEM patch (flat facets here,
    since the two-quad model has no cylinder) — the shell fed to the STEP writer."""
    from ada.fem.formats.mesh_faces import iter_fem_analytic_faces
    from ada.geom.surfaces import AdvancedFace

    a = _two_coplanar_quads()
    faces = list(iter_fem_analytic_faces(a))
    # non-cylinder patch → coplanar-merged: the two coplanar quads collapse to ONE flat face.
    assert len(faces) == 1
    assert all(isinstance(f, AdvancedFace) for f in faces)


def test_to_stp_cylinder_strategy_routes_through_analytic_emit(tmp_path):
    """to_stp(writer='stream', merge_strategy='cylinder') routes the FEM through the
    analytic emit (one recognised-surface shell) — cylinders on tubes, flat facets on
    the rest — producing a valid STEP without building Plate objects."""
    a = _two_coplanar_quads()
    out = tmp_path / "m.step"
    stats = a.to_stp(str(out), writer="stream", fuse_fem=True, merge_strategy="cylinder")
    assert stats["emitted"] >= 1 and stats["skipped"] == 0
    txt = out.read_text()
    assert "ADVANCED_FACE" in txt  # analytic faces (flat facets here; CYLINDRICAL_SURFACE on real tubes)
    assert txt.rstrip().endswith("END-ISO-10303-21;")


def test_cylinder_trim_faces_bounds_by_real_boundary():
    """cylinder_trim_faces trims the tube by its ACTUAL boundary loops (here the two end
    rings) → one CYLINDRICAL_SURFACE face with a FaceBound per loop, edges arcs/lines (no
    B-spline), emitting valid STEP."""
    import os
    import tempfile

    from ada.cadit.step.write.ap242_stream import Ap242StreamWriter
    from ada.fem.formats.mesh_faces import cylinder_trim_faces, fit_cylinder_params
    from ada.geom.surfaces import AdvancedFace, CylindricalSurface, OpenShell, ShellBasedSurfaceModel

    prims, patch = _synthetic_cylinder_prims(r=2.0, length=5.0)
    cf = fit_cylinder_params(prims, patch)
    faces = cylinder_trim_faces(prims, patch, cf)
    assert faces is not None and len(faces) == 1
    f = faces[0]
    assert isinstance(f, AdvancedFace) and isinstance(f.face_surface, CylindricalSurface)
    assert len(f.bounds) == 2  # trimmed by the two end rings

    shell = ShellBasedSurfaceModel(sbsm_boundary=[OpenShell(cfs_faces=faces)])
    out = tempfile.mktemp(suffix=".step")
    try:
        with open(out, "w") as fh:
            w = Ap242StreamWriter(fh, schema="AP242", assembly=True)
            with w:
                assert w.add_solid_instances(shell, name="tube", color=None, instances=[(None, None)]) == 1
        with open(out) as fh:
            txt = fh.read()
        assert txt.count("CYLINDRICAL_SURFACE") == 1
        assert "B_SPLINE" not in txt  # constant-z end rings → circular arcs only
        assert txt.rstrip().endswith("END-ISO-10303-21;")
    finally:
        if os.path.exists(out):
            os.unlink(out)


def test_cylinder_trim_faces_tessellate_on_cylinder():
    """A trimmed cylinder tessellates ON the cylinder wall (radius ~r) via its edge pcurves —
    on whichever CAD backend is active (adacpp routes the kind-6 pcurve through edge_from_pcurve;
    OCC uses it directly). Guards against the diagonal-cut face meshing flat/degenerate."""
    import numpy as np

    from ada.cad import active_backend
    from ada.fem.formats.mesh_faces import cylinder_trim_faces, fit_cylinder_params
    from ada.geom import Geometry
    from ada.geom.surfaces import OpenShell, ShellBasedSurfaceModel

    r = 2.0
    prims, patch = _synthetic_cylinder_prims(r=r, length=5.0)
    faces = cylinder_trim_faces(prims, patch, fit_cylinder_params(prims, patch))
    shell = ShellBasedSurfaceModel(sbsm_boundary=[OpenShell(cfs_faces=faces)])
    be = active_backend()
    mesh = be.tessellate(be.build(Geometry(id="tube", geometry=shell, color=None, transforms=None)))
    pos = np.asarray(mesh.positions, dtype=float).reshape(-1, 3)
    assert pos.size, "no vertices"
    radii = np.hypot(pos[:, 0], pos[:, 1])
    # the whole clean tube wall meshes on the cylinder — a collapse/degenerate mesh dips inside r
    assert radii.min() >= r - 0.1, f"vertices collapsed toward the axis (min radius {radii.min():.2f} << {r})"
    assert pos[:, 2].max() - pos[:, 2].min() >= 4.5, "meshed only a sliver, not the full wall"


def _curved_grid_block(nu, nv, curve=0.4, extra_quads=None):
    """A _ShellBlock of an (nu-1)x(nv-1) quad grid, curved in z by ``curve``; ``extra_quads``
    appends raw node-index quads (e.g. a folded stiffener) sharing grid nodes."""
    import numpy as np

    from ada.fem.formats.mesh_faces import _ShellBlock

    coords: list = []
    idx: dict = {}

    def nid(i, j):
        if (i, j) not in idx:
            idx[(i, j)] = len(coords)
            coords.append((float(i), float(j), curve * np.sin(i * 0.7)))
        return idx[(i, j)]

    conn = [[nid(i, j), nid(i + 1, j), nid(i + 1, j + 1), nid(i, j + 1)] for i in range(nu - 1) for j in range(nv - 1)]
    n_grid = len(conn)
    if extra_quads:
        for q in extra_quads(nid, coords):
            conn.append(q)
    m = len(conn)
    return (
        _ShellBlock(
            coords=np.array(coords, dtype=float),
            conn=np.array(conn, dtype=int),
            el_ids=np.arange(m),
            materials=["m"] * m,
            thicknesses=np.full(m, 0.01),
        ),
        n_grid,
    )


def test_reconstruct_curved_panels_grids_a_curved_patch():
    from ada.fem.formats.mesh_faces import _reconstruct_curved_panels
    from ada.geom.surfaces import BSplineSurfaceWithKnots

    blk, n_grid = _curved_grid_block(6, 7, curve=0.4)
    panels = _reconstruct_curved_panels(blk, set(), 6, 30.0, 12)
    assert len(panels) == 1 and isinstance(panels[0][0].face_surface, BSplineSurfaceWithKnots)
    assert len(panels[0][1]) == n_grid  # the whole curved grid → one B-spline panel


def test_reconstruct_curved_panels_skips_flat():
    # a flat grid is left to the planar merge (a degree-1 B-spline of a flat panel gains nothing)
    from ada.fem.formats.mesh_faces import _reconstruct_curved_panels

    blk, _ = _curved_grid_block(6, 7, curve=0.0)
    assert _reconstruct_curved_panels(blk, set(), 6, 30.0, 12) == []


def test_reconstruct_curved_panels_crosses_stiffener_tjunction():
    # a folded stiffener quad attached along an interior hull edge makes that edge degree-3;
    # the panel must grow ACROSS it (one panel), not fragment.
    def _stiffener(nid, coords):
        a, b = nid(3, 0), nid(3, 1)  # interior grid edge
        up1, up2 = len(coords), len(coords) + 1
        coords.append((3.0, 0.0, 5.0))
        coords.append((3.0, 1.0, 5.0))
        return [[a, b, up2, up1]]  # vertical web → ~90° fold from the hull

    from ada.fem.formats.mesh_faces import _reconstruct_curved_panels
    from ada.geom.surfaces import BSplineSurfaceWithKnots

    blk, n_grid = _curved_grid_block(6, 7, curve=0.4, extra_quads=_stiffener)
    panels = _reconstruct_curved_panels(blk, set(), 6, 30.0, 12)
    assert len(panels) == 1 and isinstance(panels[0][0].face_surface, BSplineSurfaceWithKnots)
    assert len(panels[0][1]) == n_grid  # whole hull grid captured despite the stiffener T-junction


def test_non_fem_source_rejected():
    with pytest.raises(ValueError, match="FEM source"):
        run_utility(
            "merge-preview", "model.step", storage=_FakeStore(), scope=None, on_progress=lambda *_: None, kwargs={}
        )


def test_unimplemented_algorithm_raises(monkeypatch, tmp_path):
    a = _two_coplanar_quads()
    monkeypatch.setattr(ada, "from_fem", lambda *_a, **_k: a)
    src = tmp_path / "m.fem"
    src.write_text("stub")
    with pytest.raises(NotImplementedError):
        run_utility(
            "merge-preview",
            str(src),
            storage=_FakeStore(),
            scope=None,
            on_progress=lambda *_: None,
            kwargs={"algorithm": "panel"},
        )
