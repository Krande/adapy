"""Backend plugin registry + discovery scaffold (``ada.plugins``).

Core adapy ships NO built-in backend plugins, so every registry is empty until a
plugin registers — behaviour is unchanged. These tests exercise the contract a
plugin builds against: idempotent registration, the heartbeat spec shape, the
reserved sidecar prefix, artefact contributors, entry-point discovery isolation,
and the static ``builtin_plugin_specs`` fallback (which must import WITHOUT
``ada`` so it runs in the slim API image)."""

from __future__ import annotations

import pytest

from ada.plugins import (
    PLUGIN_ENTRY_POINT_GROUP,
    discover_plugins,
    plugin_artefact_contributors,
    plugin_backend_specs,
    register_plugin_artefact_contributor,
    register_plugin_backend,
    reserved_sidecar_prefix,
    reset_registry,
)


@pytest.fixture(autouse=True)
def _clean_registry():
    reset_registry()
    yield
    reset_registry()


def test_core_ships_no_builtin_backend_plugins():
    assert plugin_backend_specs() == []
    assert plugin_artefact_contributors() == []


def test_register_backend_plugin_is_idempotent_and_carries_slug():
    register_plugin_backend("sample", name="Sample", sidecar_prefix="smpl", schema_version=15)
    spec = plugin_backend_specs()[0]
    assert spec["sidecar_prefix"] == "smpl"
    assert spec["schema_version"] == 15
    # A second registration REPLACES (not duplicates) the entry wholesale.
    register_plugin_backend("sample", name="Sample v2")
    specs = plugin_backend_specs()
    assert len(specs) == 1
    spec = specs[0]
    # ``slug`` == id so the REST union primitive (keyed by slug) folds it.
    assert spec["slug"] == "sample"
    assert spec["id"] == "sample"
    assert spec["name"] == "Sample v2"
    # Fields omitted on the replacing call revert to defaults (full replace).
    assert spec["sidecar_prefix"] == "sample"
    assert spec["schema_version"] is None


def test_extra_flags_are_advertised_verbatim():
    register_plugin_backend("x", worker_capability="capx", foo="bar")
    spec = plugin_backend_specs()[0]
    assert spec["worker_capability"] == "capx"
    assert spec["foo"] == "bar"


def test_reserved_sidecar_prefix_defaults_to_id_and_honours_alias():
    register_plugin_backend("plain")
    register_plugin_backend("aliased", sidecar_prefix="cap")
    assert reserved_sidecar_prefix("plain") == "plain"
    assert reserved_sidecar_prefix("aliased") == "cap"
    # Unknown plugin falls back to its own id.
    assert reserved_sidecar_prefix("unknown") == "unknown"


def test_register_backend_plugin_rejects_bad_id():
    with pytest.raises(ValueError):
        register_plugin_backend("")


def test_artefact_contributor_registration_and_ordering():
    register_plugin_artefact_contributor("a", lambda ctx: {"from": "a", "src": ctx["src"]})
    register_plugin_artefact_contributor("b", lambda ctx: None)
    contributors = plugin_artefact_contributors()
    assert [pid for pid, _ in contributors] == ["a", "b"]
    a_contribute = contributors[0][1]
    assert a_contribute({"src": "model.sif"}) == {"from": "a", "src": "model.sif"}
    with pytest.raises(TypeError):
        register_plugin_artefact_contributor("bad", object())  # not callable


def _stub_mesh_geom():
    # build_manifest only touches ``mesh_geom.points.shape[0]`` and
    # ``mesh_geom.cell_blocks[*].data.shape[0]`` — a duck-typed stub avoids
    # constructing the full reader dataclass.
    import types

    import numpy as np

    return types.SimpleNamespace(
        points=np.zeros((3, 3), dtype="float32"),
        cell_blocks=[types.SimpleNamespace(data=np.zeros((1, 3), dtype="int64"))],
    )


def test_build_manifest_folds_contributions_under_reserved_plugins_map():
    # The artefact-contributor hook: a registered contributor's return lands under
    # manifest["plugins"][id]; a None contribution adds nothing.
    register_plugin_artefact_contributor("cap", lambda ctx: {"runs": 3, "src": ctx["src"]})
    register_plugin_artefact_contributor("quiet", lambda ctx: None)

    from ada.fem.results.artefacts import build_manifest

    manifest = build_manifest("model.sif", _stub_mesh_geom(), "mesh.glb", [])
    assert manifest["plugins"]["cap"] == {"runs": 3, "src": "model.sif"}
    assert "quiet" not in manifest["plugins"]


def test_build_manifest_has_no_plugins_key_without_contributors():
    from ada.fem.results.artefacts import build_manifest

    manifest = build_manifest("model.sif", _stub_mesh_geom(), "mesh.glb", [])
    assert "plugins" not in manifest


def test_discover_plugins_isolates_a_failing_entry_point(monkeypatch):
    # A broken plugin entry point is logged + skipped, never aborting discovery.
    class _EP:
        def __init__(self, name, fn):
            self.name = name
            self._fn = fn

        def load(self):
            return self._fn

    def _boom():
        raise RuntimeError("bad plugin")

    seen = []

    def _ok():
        seen.append(True)
        register_plugin_backend("ok-plugin")

    # discover_plugins does ``from importlib.metadata import entry_points`` at
    # call time, so patching the source module is what it resolves.
    import importlib.metadata as md

    monkeypatch.setattr(md, "entry_points", lambda **_: [_EP("boom", _boom), _EP("ok", _ok)])
    run = discover_plugins()
    assert run == ["ok"]
    assert seen == [True]
    assert any(s["slug"] == "ok-plugin" for s in plugin_backend_specs())


def test_builtin_plugin_specs_fallback_is_empty_and_ada_free():
    # The static fallback must be importable without ``ada`` (slim API image).
    from ada.comms.rest.catalog import builtin_plugin_specs

    assert builtin_plugin_specs() == []


def test_entry_point_group_name():
    assert PLUGIN_ENTRY_POINT_GROUP == "ada.plugins"
