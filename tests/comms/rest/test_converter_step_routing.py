"""FEM sources must be routed through the streaming STEP writer.

The OCC XCAF writer OOMs on large FEM meshes; the streaming AP242 writer holds
constant memory. `_via_ada_to_step` should select `writer="stream"` for FEM
sources and keep the default OCC writer for everything else.
"""

import pathlib

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
    renders straight from this — no hardcoded vocabulary."""
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
        assert "pyocc" in tess["enum_by"]["python"] and "cgal" in tess["enum_by"]["python"]
        assert tess["enum_by"]["wasm"] == ["wasm-native", "pyodide"]
    # STEP exposes the OCC streaming reader as an extra python kernel; generic does not.
    step_tess = _serializer_tessellator(".step")[1]
    ifc_tess = _serializer_tessellator(".ifc")[1]
    assert "occ" in step_tess["enum_by"]["python"]
    assert "occ" not in ifc_tess["enum_by"]["python"]
    # new names are in the API allowlist
    assert {"serializer", "tessellator"} <= conv.ConverterRegistry.all_options()


def test_apply_glb_serializer_resolves_to_engine_knobs():
    """The serializer/tessellator tokens fold into the existing engine knobs;
    unset tokens leave explicit knobs untouched (full back-compat)."""
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
