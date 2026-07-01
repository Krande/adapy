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
    assert set(kw["algorithm"]["enum"]) == {"none", "coplanar", "surface", "panel"}
    assert set(kw["mode"]["enum"]) == {"status", "achieved", "component"}


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
