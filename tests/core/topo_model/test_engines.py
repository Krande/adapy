"""Procedural-engine resolution + dispatch (the echo-engine slice)."""

from __future__ import annotations

import pytest

from ada.topo_model import engines
from ada.topo_model.wasm_compile import compile_doc

DOC = {
    "spaces": [
        {"NAME": "Cell1", "X": 0, "Y": 0, "Z": 0, "DX": 5, "DY": 5, "DZ": 3},
        {"NAME": "Cell2", "X": 5, "Y": 0, "Z": 0, "DX": 5, "DY": 5, "DZ": 3},
    ]
}


def _is_glb(b: bytes) -> bool:
    return b[:4] == b"glTF"


# --- resolver -------------------------------------------------------------- #
def test_entrypoint_for_builtin_and_passthrough():
    assert engines.entrypoint_for("echo") == "ada.topo_model.echo_engine:compile_doc"
    # an explicit module:callable passes straight through (registry manifest form)
    assert engines.entrypoint_for("some.mod:fn") == "some.mod:fn"


def test_entrypoint_for_unknown_raises():
    with pytest.raises(ValueError, match="unknown procedural engine"):
        engines.entrypoint_for("nope")


def test_entrypoint_for_an_engine_announced_in_process(monkeypatch):
    # A plugin wheel's register hook (the browser) or a preloaded module (a local
    # server) announces an engine with its entrypoint; the slug then resolves to it,
    # so a compile dispatches by slug there as it does on a worker.
    from ada.topo_model import engine_catalog

    monkeypatch.setitem(
        engine_catalog._ENGINE_CAPABILITY_REGISTRY,
        "demo-engine",
        {"slug": "demo-engine", "name": "Demo", "entrypoint": "ada.topo_model.echo_engine:compile_doc"},
    )
    assert engines.entrypoint_for("demo-engine") == "ada.topo_model.echo_engine:compile_doc"
    assert _is_glb(compile_doc(DOC, engine="demo-engine"))
    # flags-only registrations (no entrypoint) still do not make a slug resolvable
    monkeypatch.setitem(engine_catalog._ENGINE_CAPABILITY_REGISTRY, "flags-only", {"slug": "flags-only"})
    with pytest.raises(ValueError, match="unknown procedural engine"):
        engines.entrypoint_for("flags-only")


def test_load_entrypoint_validates_form():
    with pytest.raises(ValueError, match="module:callable"):
        engines.load_entrypoint("no_colon_here")
    fn = engines.load_entrypoint("ada.topo_model.echo_engine:compile_doc")
    assert callable(fn)


def test_default_engine_not_routed_through_compile_with_engine():
    assert engines.is_default_engine(None) and engines.is_default_engine("adapy-default")
    with pytest.raises(ValueError, match="non-default"):
        engines.compile_with_engine("adapy-default", DOC)


# --- dispatch (server-side path) ------------------------------------------ #
def test_compile_with_engine_echo_renders_boxes():
    glb = engines.compile_with_engine("echo", DOC)
    assert _is_glb(glb)
    # echo is the raw-boxes ("none" blueprint) render — strictly smaller than the
    # full steel structure the default engine builds for the same doc.
    default = compile_doc(DOC)
    assert _is_glb(default)
    assert len(glb) < len(default)


# --- dispatch (wasm entrypoint path) -------------------------------------- #
def test_wasm_compile_doc_engine_selection():
    # The in-browser entrypoint routes a non-default engine through the same
    # resolver; echo via the entrypoint matches echo via the resolver in size.
    via_entry = compile_doc(DOC, engine="echo")
    via_resolver = engines.compile_with_engine("echo", DOC)
    assert _is_glb(via_entry)
    assert len(via_entry) == len(via_resolver)
    # default (engine=None) differs from echo
    assert len(compile_doc(DOC, engine=None)) != len(via_entry)


def test_wasm_compile_doc_unknown_engine_raises():
    with pytest.raises(ValueError, match="unknown procedural engine"):
        compile_doc(DOC, engine="does-not-exist")


# --- EngineBinding (routing/identity header) ------------------------------ #
def test_engine_binding_defaults():
    b = engines.EngineBinding()
    assert b.engine == engines.DEFAULT_ENGINE_SLUG
    assert b.schema_version == engines.PROCEDURAL_SCHEMA_VERSION


def test_engine_binding_major_compat():
    cur = engines.PROCEDURAL_SCHEMA_VERSION  # "1.0"
    assert engines.EngineBinding(schema_version="1.7").is_compatible(cur)  # minor bump = OK
    assert not engines.EngineBinding(schema_version="2.0").is_compatible(cur)  # major bump = break
