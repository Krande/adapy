"""FEM sources must be routed through the streaming STEP writer.

The OCC XCAF writer OOMs on large FEM meshes; the streaming AP242 writer holds
constant memory. `_via_ada_to_step` should select `writer="stream"` for FEM
sources and keep the default OCC writer for everything else.
"""

import pathlib

import pytest

import ada
from ada.api.spatial.part import Part
from ada.comms.rest import converter as conv


def _captured_writer(monkeypatch, source_ext: str) -> str:
    model = ada.Assembly("a") / (ada.Part("p") / ada.Plate("pl", [(0, 0), (1, 0), (1, 1), (0, 1)], 0.01))

    monkeypatch.setattr(conv, "_load_with_ada", lambda src, ext: model)
    monkeypatch.setattr(conv, "_apply_fem_to_objects", lambda *a, **k: None)

    recorded = {}

    def fake_to_stp(self, dest, *args, writer="occ", **kwargs):
        recorded["writer"] = writer
        # Emit a deck carrying a solid ROOT — the non-FEM path now re-checks the OCC
        # output and only falls back to the streaming faceted writer when it emitted
        # NO solid (e.g. an alignment sweep adacpp can't build). A realistic writer
        # produces a solid, so the occ routing must stick.
        pathlib.Path(dest).write_text(
            "ISO-10303-21;\nDATA;\n#1=MANIFOLD_SOLID_BREP('s',#2);\nENDSEC;\nEND-ISO-10303-21;\n"
        )
        return {"emitted": 1, "skipped": 0}

    monkeypatch.setattr(Part, "to_stp", fake_to_stp)

    out = conv._via_ada_to_step(pathlib.Path(f"dummy{source_ext}"), source_ext, lambda *a, **k: None)
    # _via_ada_to_step now returns the path of the STEP file it wrote (ownership
    # transfers to the caller); the streaming-upload contract.
    assert isinstance(out, pathlib.Path)
    assert out.read_bytes()  # the fake writer wrote a non-empty deck
    out.unlink()
    return recorded["writer"]


def test_fem_source_uses_stream_writer(monkeypatch):
    assert _captured_writer(monkeypatch, ".fem") == "stream"


def test_inp_source_uses_stream_writer(monkeypatch):
    assert _captured_writer(monkeypatch, ".inp") == "stream"


def test_non_fem_source_uses_occ_writer(monkeypatch):
    assert _captured_writer(monkeypatch, ".step") == "occ"
    assert _captured_writer(monkeypatch, ".ifc") == "occ"


def test_glb_target_exposes_tessellation_quality_options():
    """The configurable tessellation-quality knobs are surfaced on any → GLB pair and
    map to the ADA_OCC_TESS_* env the worker / tessellator read."""
    names = {o["name"] for o in conv.ConverterRegistry.options_for(".step", "glb")}
    assert {"tess_linear_deflection", "tess_angular_deg", "tess_relative"} <= names
    # and they are in the global allowlist used by the API validator
    assert "tess_linear_deflection" in conv.ConverterRegistry.all_options()


def _serializer_tessellator(source_ext: str):
    opts = conv.ConverterRegistry.options_for(source_ext, "glb")
    ser = next(o for o in opts if o["name"] == "serializer")
    tess = next(o for o in opts if o["name"] == "tessellator")
    return ser, tess


def test_glb_serializer_tessellator_advertised_single_source():
    """The reconvert dropdowns are data-driven: every → GLB row advertises a
    serializer enum with labels + a client/server runtime split, and a
    dependent tessellator enum keyed by serializer (enum_by). The frontend
    renders straight from this — no hardcoded vocabulary.

    Asserts the SHAPE only. The tessellator vocabulary is discovered from adacpp, so its VALUES
    differ per environment (this suite runs with and without adacpp installed) — pinning them here
    is what makes a test env-dependent. `test_worker_advert_*` covers the values, against a
    synthetic worker whose capability is stated rather than probed.
    """
    for ext in (".step", ".ifc", ".sat"):
        ser, tess = _serializer_tessellator(ext)
        assert ser["type"] == "enum" and tess["type"] == "enum"
        assert ser["default"] == "cpp"
        assert set(ser["enum"]) == {"cpp", "python", "wasm"}
        # labels + runtime per serializer value
        assert set(ser["labels"]) == set(ser["enum"])
        assert ser["runtime"]["wasm"] == "client"
        assert ser["runtime"]["cpp"] == "server" and ser["runtime"]["python"] == "server"
        # dependent tessellator: enum_by keyed by serializer, depends_on wired
        assert tess["depends_on"] == "serializer"
        assert set(tess["enum_by"]) == set(ser["enum"])
        assert tess["enum_by"]["cpp"] == ["native"]
        assert tess["enum_by"]["wasm"] == ["wasm-native", "pyodide"]
        # the python serializer offers whatever this env discovered — never nothing, and every
        # token it offers must be describable (the SPA renders labels straight from this).
        assert tess["enum_by"]["python"], "the python serializer must always offer a kernel"
        assert set(tess["enum_by"]["python"]) <= set(tess["enum"])
        assert set(tess["labels"]) >= set(tess["enum"])
    # Same kernels for every source family. The pre-discovery table split step/generic, but only to
    # list a duplicate `occ` alias on STEP; each real kernel reaches both (STEP via
    # step_glb_pipeline, generic via glb_tess_engine).
    step_tess = _serializer_tessellator(".step")[1]
    ifc_tess = _serializer_tessellator(".ifc")[1]
    assert step_tess["enum_by"]["python"] == ifc_tess["enum_by"]["python"]
    # new names are in the API allowlist
    assert {"serializer", "tessellator"} <= conv.ConverterRegistry.all_options()


def test_apply_glb_serializer_resolves_to_engine_knobs():
    """The serializer/tessellator tokens fold into the existing engine knobs;
    unset tokens leave explicit knobs untouched (full back-compat).

    Resolution is VOCABULARY, not capability: the same token resolves to the same engine whether or
    not adacpp is importable here. It used to look the track up in available_tess_tracks() and fall
    back to libtess2 when absent, which made a stored `cgal` config mean adacpp-cgal in one process
    and libtess2 in another — and libtess2 is itself adacpp, so it could never be the right answer
    for a pool that lacked adacpp. Capability is enforced separately (see the gating tests).
    """
    ap = conv._apply_glb_serializer
    # cpp/STEP -> adacpp-native pipeline, server path
    assert ap(".step", "cpp", None, step_glb_pipeline=None, glb_tess_engine=None) == (
        conv._STEP_GLB_PIPELINE_ADACPP_NATIVE,
        None,
        False,
    )
    # python/STEP kernels map to the STEP pipeline + force the python path
    assert ap(".step", "python", "pyocc", step_glb_pipeline=None, glb_tess_engine=None) == (
        conv._STEP_GLB_PIPELINE_OCC,
        None,
        True,
    )
    assert ap(".step", "python", "cgal", step_glb_pipeline=None, glb_tess_engine=None) == (
        conv._STEP_GLB_PIPELINE_ADACPP_CGAL,
        None,
        True,
    )
    # python/IFC maps to the BatchTessellator engine + forces python (bypass native ifc->glb)
    assert ap(".ifc", "python", "ifc-hybrid", step_glb_pipeline=None, glb_tess_engine=None) == (
        None,
        conv._STEP_GLB_PIPELINE_ADACPP_HYBRID,
        True,
    )
    # client serializer never resolves server-side (routed in-browser by the SPA)
    assert ap(".ifc", "wasm", "pyodide", step_glb_pipeline=None, glb_tess_engine=None) == (None, None, False)
    # unset serializer keeps explicit knobs + defaults
    assert ap(".step", None, None, step_glb_pipeline="occ-builtin", glb_tess_engine=None) == (
        "occ-builtin",
        None,
        False,
    )


def test_token_resolution_is_env_independent():
    """A track name resolves structurally, so a token that postdates this module (adacpp:cdt) and a
    token whose kernel isn't installed both still mean what they say."""
    assert conv._tess_token_to_pipeline("adacpp:cdt") == "adacpp-cdt"
    assert conv._pipeline_to_track_name("adacpp-cdt") == "adacpp:cdt"
    # the four historic identities are pinned, and round-trip
    for track, pipe in conv._HISTORIC_TRACK_PIPELINE.items():
        assert conv._tess_token_to_pipeline(f"adacpp:{track}") == pipe
        assert conv._pipeline_to_track_name(pipe) == f"adacpp:{track}"
    # adapy's own OCC track is not an adacpp pipeline
    assert conv._tess_token_to_pipeline("occ") == conv._STEP_GLB_PIPELINE_OCC
    assert conv._pipeline_to_track_name(conv._STEP_GLB_PIPELINE_OCC) is None
    # adacpp-native is a whole-file code path, not a track
    assert conv._pipeline_to_track_name(conv._STEP_GLB_PIPELINE_ADACPP_NATIVE) is None


def test_glb_engine_stream_value_covers_discovered_tracks():
    """The non-STEP engine → ADA_STREAM_TESS_PIPELINE mapping is derived, not a table.

    The table it replaced listed the four engines that existed when it was written, so a track
    discovered later resolved to None and SILENTLY ran the OCC BatchTessellator instead — the user
    picked a kernel and got a different one, with nothing logged.
    """
    assert conv._glb_engine_to_stream("adacpp-cdt") == "cdt"
    assert conv._glb_engine_to_stream("libtess2") == "libtess2"
    assert conv._glb_engine_to_stream("adacpp-cgal") == "cgal"
    # occ-builtin means "no stream override" (the OCC BatchTessellator default)
    assert conv._glb_engine_to_stream("occ-builtin") is None
    # every advertised engine except occ-builtin must map to a real stream pipeline
    for engine in conv._glb_tess_engines():
        if engine != conv._STEP_GLB_PIPELINE_OCC:
            assert conv._glb_engine_to_stream(engine), f"{engine} advertised but resolves to no kernel"


def test_brep_target_exposes_serializer_writer_axis():
    """B-rep→B-rep rows (step→ifc, ifc→step) advertise the same shared selector, but the 2nd axis is
    titled 'Writer' (no tessellation) and mirrors the serializer 1:1."""
    for frm, to in ((".step", "ifc"), (".ifc", "step")):
        opts = conv.ConverterRegistry.options_for(frm, to)
        by_name = {o["name"]: o for o in opts}
        # not every build registers native ifc→step; skip when the path options aren't present
        if "serializer" not in by_name:
            continue
        ser, wr = by_name["serializer"], by_name["tessellator"]
        assert ser["title"] == "Serializer"
        assert set(ser["enum"]) == {"cpp", "python", "wasm"}
        assert ser["runtime"]["wasm"] == "client"
        # 2nd axis is DISPLAYED as Writer (wire key stays 'tessellator' for a unified resolver)
        assert wr["title"] == "Writer"
        assert wr["depends_on"] == "serializer"
        assert wr["enum_by"] == {"cpp": ["native"], "python": ["occ"], "wasm": ["wasm-native"]}
    # python serializer → the OCC writer branch; cpp/wasm do not
    assert conv._brep_writer_is_python("python") is True
    assert conv._brep_writer_is_python("cpp") is False
    assert conv._brep_writer_is_python("wasm") is False


# --------------------------------------------------------------------------- #
# Worker capability advertisement.
#
# These build the option schema themselves rather than reading the local registry, so what a worker
# "can run" is STATED, not probed. That is the whole point: the local registry answers differently
# depending on whether adacpp/pythonocc happen to be importable in the current test env, and a test
# that reads it is really asserting on its own environment. It also lets us model a heterogeneous
# cluster (a thin pool + a full pool), which no single test env can be.
# --------------------------------------------------------------------------- #

_FULL_POOL_TESS = ["adacpp:libtess2", "adacpp:cdt", "adacpp:cgal", "occ"]


def _serializer_schema(python_tess: list[str]) -> list[dict]:
    """A →GLB option pair shaped exactly like _glb_serializer_options() emits."""
    enum_by = {"cpp": ["native"], "python": list(python_tess), "wasm": ["wasm-native", "pyodide"]}
    tokens = [t for s in ("cpp", "python", "wasm") for t in enum_by[s]]
    tokens = list(dict.fromkeys(tokens))
    return [
        {
            "name": "serializer",
            "type": "enum",
            "title": "Serializer",
            "default": "cpp",
            "enum": ["cpp", "python", "wasm"],
            "labels": {"cpp": "C++", "python": "Python", "wasm": "WASM"},
            "runtime": {"cpp": "server", "python": "server", "wasm": "client"},
        },
        {
            "name": "tessellator",
            "type": "enum",
            "title": "Tessellator",
            "default": enum_by["cpp"][0],
            "enum": tokens,
            "labels": {t: f"label:{t}" for t in tokens},
            "descriptions": {t: f"desc:{t}" for t in tokens},
            "enum_by": enum_by,
            "depends_on": "serializer",
        },
    ]


def _conversions(python_tess: list[str], engines: list[str]) -> list[dict]:
    return [
        {
            "from": ".step",
            "to": ["glb"],
            "options": {
                "glb": _serializer_schema(python_tess)
                + [{"name": "step_glb_pipeline", "type": "enum", "default": "adacpp-native", "enum": list(engines)}],
            },
        }
    ]


def _gate(monkeypatch, *, tokens: set[str], engines: list[str], python_tess=None):
    from ada.comms.rest import worker as wk

    monkeypatch.setattr(conv, "available_tess_tokens", lambda: frozenset(tokens))
    monkeypatch.setattr(conv, "available_step_glb_pipelines", lambda: tuple(engines))
    rows = _conversions(python_tess or _FULL_POOL_TESS, ["adacpp-native", "libtess2", "occ-builtin", "adacpp-cgal"])
    gated = wk._gate_advertised_engines(rows)
    return {o["name"]: o for o in gated[0]["options"]["glb"]}


def test_worker_gates_tessellator_not_just_the_pipeline(monkeypatch):
    """A pool advertises only kernels it has a build of.

    Gating stopped at `step_glb_pipeline`, so the serializer/tessellator dropdowns — the ones the
    SPA actually renders — kept advertising every kernel the registry knew. A job routed here on
    that advert and silently fell back to another kernel.
    """
    by_name = _gate(
        monkeypatch,
        tokens={"occ"},  # a pool with pythonocc but no adacpp
        engines=["occ-builtin"],
    )
    tess, ser = by_name["tessellator"], by_name["serializer"]
    assert tess["enum_by"]["python"] == ["occ"]
    assert "adacpp:cdt" not in tess["enum"] and "adacpp:cgal" not in tess["enum"]
    # cpp pins adacpp's in-process writer, so it goes with adacpp
    assert "cpp" not in ser["enum"] and "cpp" not in tess["enum_by"]
    assert ser["default"] == "python", "the default must move off a serializer this pool dropped"
    assert tess["default"] == "occ"
    # the engine knob is still gated (the behaviour that already existed)
    assert by_name["step_glb_pipeline"]["enum"] == ["occ-builtin"]
    # labels/descriptions follow the surviving tokens — no orphans
    assert set(tess["labels"]) == set(tess["enum"])


def test_worker_never_gates_client_engines(monkeypatch):
    """wasm runs in the USER'S BROWSER. A worker without adacpp must not strip it.

    available_tess_tokens() deliberately omits the client tokens (no server probe can answer for
    them), so gating them against it would drop every one — and the SPA would lose the in-browser
    option because a server pool was thin.
    """
    by_name = _gate(monkeypatch, tokens={"occ"}, engines=["occ-builtin"])
    tess, ser = by_name["tessellator"], by_name["serializer"]
    assert tess["enum_by"]["wasm"] == ["wasm-native", "pyodide"]
    assert "wasm" in ser["enum"] and ser["runtime"]["wasm"] == "client"
    assert {"wasm-native", "pyodide"} <= set(tess["enum"])


def test_worker_advertises_every_discovered_track(monkeypatch):
    """A track that postdates this module rides through gating untouched."""
    by_name = _gate(monkeypatch, tokens=set(_FULL_POOL_TESS) | {"native"}, engines=["adacpp-native", "libtess2"])
    tess = by_name["tessellator"]
    assert "adacpp:cdt" in tess["enum_by"]["python"]
    assert by_name["serializer"]["default"] == "cpp" and tess["default"] == "native"


def test_worker_gating_does_not_mutate_the_shared_registry(monkeypatch):
    """The registry's option dicts are module-level and shared; gating must deep-copy."""
    from ada.comms.rest import worker as wk

    monkeypatch.setattr(conv, "available_tess_tokens", lambda: frozenset({"occ"}))
    monkeypatch.setattr(conv, "available_step_glb_pipelines", lambda: ("occ-builtin",))
    rows = _conversions(_FULL_POOL_TESS, ["adacpp-native", "libtess2"])
    wk._gate_advertised_engines(rows)
    assert rows[0]["options"]["glb"][1]["enum_by"]["python"] == _FULL_POOL_TESS


# --------------------------------------------------------------------------- #
# API-side merge across a heterogeneous cluster.
# --------------------------------------------------------------------------- #


def _merged(*pools: list[str]) -> dict:
    """Merge N pools' `tessellator` option the way the API's registry merge does."""
    cur = None
    for tess in pools:
        opt = {o["name"]: o for o in _serializer_schema(tess)}["tessellator"]
        if cur is None:
            cur = opt
        else:
            conv.merge_option_into(cur, opt)
    return cur


def test_api_merge_unions_enum_by_across_pools():
    """A thin pool registering first must not pin the cluster's vocabulary.

    The merge unioned `enum` but took FIRST-WRITER for `enum_by`, so a thin pool's
    enum_by['python'] == ['occ'] won for the whole cluster while the full pool's tracks were still
    unioned into `enum` — reaching the dropdown with no serializer offering them, i.e. rendered and
    unselectable.
    """
    merged = _merged(["occ"], _FULL_POOL_TESS)
    assert set(merged["enum_by"]["python"]) == set(_FULL_POOL_TESS)
    assert set(merged["enum"]) >= set(_FULL_POOL_TESS)
    # every advertised token is describable regardless of which pool contributed it
    assert set(merged["labels"]) >= set(merged["enum"])
    assert set(merged["descriptions"]) >= set(merged["enum"])


def test_api_merge_is_order_independent():
    """Whichever pool happens to register first, the cluster advertises the same thing."""
    thin_first = _merged(["occ"], _FULL_POOL_TESS)
    full_first = _merged(_FULL_POOL_TESS, ["occ"])
    assert set(thin_first["enum_by"]["python"]) == set(full_first["enum_by"]["python"])
    assert set(thin_first["enum"]) == set(full_first["enum"])


def test_wasm_engine_tokens_are_the_spa_contract():
    """The wasm serializer's engine tokens are a cross-language contract, so pin both ends.

    adapy declares them (_WASM_GLB_ENGINES) and the SPA branches on them as string literals —
    `resolved.tessellator === "wasm-native"` picks the embind pipeline over Pyodide in
    services/conversion/index.ts. Nothing else connects the two: rename the token here and the
    comparison silently stops matching, so every in-browser job quietly routes to Pyodide instead of
    the native module. That is a fallback, not an error, so no test would otherwise notice.
    """
    spa = pathlib.Path(__file__).parents[3] / "src/frontend/src/services/conversion/index.ts"
    if not spa.is_file():  # sdist / wheel test envs ship no frontend tree
        pytest.skip(f"frontend source not present at {spa}")
    src = spa.read_text(encoding="utf-8")
    assert f'=== "{conv._WASM_ENGINE_NATIVE}"' in src, (
        f"the SPA no longer branches on {conv._WASM_ENGINE_NATIVE!r}; in-browser jobs would "
        "silently fall through to Pyodide instead of the native embind module"
    )
    # Both tokens must reach the dropdown, and neither may collide with a discovered track name
    # (which would make the resolver ambiguous about whether a job is client- or server-side).
    assert conv._glb_serializer_tess("wasm") == list(conv._WASM_GLB_ENGINES)
    assert not set(conv._WASM_GLB_ENGINES) & set(conv._python_tess_tokens())


# --------------------------------------------------------------------------- #
# face_regions — the "Clickable surfaces" reconvert toggle.
# --------------------------------------------------------------------------- #


def test_face_regions_offered_only_where_it_is_produced():
    """Only the native STEP->GLB path forwards face_regions to adacpp; the python path never reads
    the flag. Advertising it against a serializer that ignores it would offer a capability that
    silently doesn't happen, so supported_by names the ones that can deliver it."""
    step = {o["name"]: o for o in conv._glb_serializer_options(".stp")}["face_regions"]
    assert step["type"] == "bool"
    assert step["default"] is False
    assert step["supported_by"] == [conv._GLB_SERIALIZER_CPP]
    assert step["depends_on"] == "serializer"

    # An IFC source's native path has no face-region support at all -> offered nowhere.
    ifc = {o["name"]: o for o in conv._glb_serializer_options(".ifc")}["face_regions"]
    assert ifc["supported_by"] == []


def test_face_regions_is_never_supported_by_a_client_serializer():
    """The in-browser serializers never reach the native path, so they can't produce regions."""
    step = {o["name"]: o for o in conv._glb_serializer_options(".stp")}["face_regions"]
    assert not (set(step["supported_by"]) & set(conv._GLB_CLIENT_SERIALIZERS))


def test_merge_unions_supported_by_across_pools():
    """A pool that can't produce face regions must not pin their absence for the cluster.

    supported_by is a per-pool capability list, so it merges like `enum` — union, not
    first-writer-wins. Registering the empty (incapable) pool FIRST is the case that broke
    enum_by before: the capable pool's answer has to survive it.
    """
    incapable = {"name": "face_regions", "type": "bool", "supported_by": []}
    capable = {"name": "face_regions", "type": "bool", "supported_by": [conv._GLB_SERIALIZER_CPP]}

    cur = dict(incapable)
    conv.merge_option_into(cur, capable)
    assert cur["supported_by"] == [conv._GLB_SERIALIZER_CPP], "the capable pool's answer must survive"

    # ...and the reverse order agrees (no duplicates).
    cur = dict(capable)
    conv.merge_option_into(cur, incapable)
    assert cur["supported_by"] == [conv._GLB_SERIALIZER_CPP]
