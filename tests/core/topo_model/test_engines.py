"""Procedural-engine resolution + dispatch.

The non-default engine here is the test-only ``raw_box_engine`` fixture
(``tests/procedural_engine_fixture.py``), registered in-process as a tool's
package registers its own engine."""

from __future__ import annotations

import pytest
from tests.procedural_engine_fixture import ENTRYPOINT

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
    assert engines.entrypoint_for("adapy-default") == "ada.topo_model.wasm_compile:compile_doc"
    # an explicit module:callable passes straight through (registry manifest form)
    assert engines.entrypoint_for("some.mod:fn") == "some.mod:fn"


def test_the_only_builtin_engine_is_the_default():
    # Every other engine is a registered row, a worker's advertisement or an
    # in-process registration -- adapy ships no diagnostic engine to pick.
    assert set(engines.BUILTIN_ENGINES) == {engines.DEFAULT_ENGINE_SLUG}


def test_entrypoint_for_unknown_raises():
    with pytest.raises(ValueError, match="unknown procedural engine"):
        engines.entrypoint_for("nope")


def test_entrypoint_for_an_engine_announced_in_process(raw_box_engine, monkeypatch):
    # A plugin wheel's register hook (the browser) or a preloaded module (a local
    # server) announces an engine with its entrypoint; the slug then resolves to it,
    # so a compile dispatches by slug there as it does on a worker.
    from ada.topo_model import engine_catalog

    assert engines.entrypoint_for(raw_box_engine) == ENTRYPOINT
    assert _is_glb(compile_doc(DOC, engine=raw_box_engine))
    # flags-only registrations (no entrypoint) still do not make a slug resolvable
    monkeypatch.setitem(engine_catalog._ENGINE_CAPABILITY_REGISTRY, "flags-only", {"slug": "flags-only"})
    with pytest.raises(ValueError, match="unknown procedural engine"):
        engines.entrypoint_for("flags-only")


def test_load_entrypoint_validates_form():
    with pytest.raises(ValueError, match="module:callable"):
        engines.load_entrypoint("no_colon_here")
    fn = engines.load_entrypoint(ENTRYPOINT)
    assert callable(fn)


def test_default_engine_not_routed_through_compile_with_engine():
    assert engines.is_default_engine(None) and engines.is_default_engine("adapy-default")
    with pytest.raises(ValueError, match="non-default"):
        engines.compile_with_engine("adapy-default", DOC)


# --- dispatch (server-side path) ------------------------------------------ #
def test_compile_with_engine_dispatches_to_the_selected_engine(raw_box_engine):
    glb = engines.compile_with_engine(raw_box_engine, DOC)
    assert _is_glb(glb)
    # the raw-boxes ("none" blueprint) render is strictly smaller than the full
    # steel structure the default engine builds for the same doc.
    default = compile_doc(DOC)
    assert _is_glb(default)
    assert len(glb) < len(default)
    # the entrypoint form dispatches to the same callable
    assert len(engines.compile_with_engine(ENTRYPOINT, DOC)) == len(glb)


# --- dispatch (wasm entrypoint path) -------------------------------------- #
def test_wasm_compile_doc_engine_selection(raw_box_engine):
    # The in-browser entrypoint routes a non-default engine through the same
    # resolver; the engine via the entrypoint matches it via the resolver in size.
    via_entry = compile_doc(DOC, engine=raw_box_engine)
    via_resolver = engines.compile_with_engine(raw_box_engine, DOC)
    assert _is_glb(via_entry)
    assert len(via_entry) == len(via_resolver)
    # default (engine=None) differs from the selected engine
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
