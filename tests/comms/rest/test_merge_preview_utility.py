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

    def put_bytes(self, key, data):
        self.blobs[key] = data


def test_registered_with_algorithm_swap_spec():
    assert "merge-preview" in UtilityRegistry.names()
    spec = next(s for s in UtilityRegistry.specs() if s["name"] == "merge-preview")
    kw = {k["name"]: k for k in spec["kwargs"]}
    assert {"algorithm", "mode", "ndigits", "angle_tol", "min_patch_quads"} <= set(kw)
    assert set(kw["algorithm"]["enum"]) == {"none", "coplanar", "planar", "surface", "classify", "panel"}
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
