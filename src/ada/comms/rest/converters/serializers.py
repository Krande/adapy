"""The serializer × tessellator option axes the SPA renders (labels, runtime, per-serializer
tokens), option-schema merging, and the resolution of a chosen serializer to engine knobs.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .pipelines import (
    _LEGACY_TESS_ALIASES,
    _STEP_GLB_PIPELINE_ADACPP_NATIVE,
    _tess_token_to_glb_engine,
    _tess_token_to_pipeline,
)

if TYPE_CHECKING:
    pass


# ---------------------------------------------------------------------------
# Serializer × Tessellator matrix — SINGLE SOURCE OF TRUTH for the SPA's
# reconvert dropdowns (gallery tools + ConversionRow). Two user-facing axes:
#
#   * serializer  — WHICH code path drives the →GLB conversion:
#         cpp      pure-C++ adacpp  (STEP: adacpp-native; IFC: native_ifc_to_glb)
#         python   Python-orchestrated (ada import + BatchTessellator/stream reader)
#         wasm     client-side, runs entirely in the browser (no server round-trip)
#   * tessellator — WHICH kernel meshes the geometry. It is DEPENDENT on the
#                   serializer (enum_by): the server `python` serializer exposes
#                   the full kernel choice; `cpp` pins libtess2; the client `wasm`
#                   serializer exposes its two in-browser engines (native embind
#                   module / Pyodide wheels).
#
# The frontend renders both dropdowns purely from the option schema advertised
# on the conversion matrix (``labels`` + ``enum_by``) and sends back opaque
# ``{serializer, tessellator}`` tokens — it hardcodes NONE of this vocabulary.
# The backend resolver (:func:`_apply_glb_serializer`) folds those tokens into
# the existing engine knobs (``step_glb_pipeline`` / ``glb_tess_engine``) plus
# the native-vs-python routing. The client serializer carries ``runtime="client"``
# so the SPA routes it to the in-browser pipeline instead of the worker; it
# never reaches this resolver.
_GLB_SERIALIZER_CPP = "cpp"


_GLB_SERIALIZER_PYTHON = "python"


_GLB_SERIALIZER_WASM = "wasm"


# Human labels for every serializer token (superset; each row advertises only
# the subset that applies to its source family).
_GLB_SERIALIZER_LABELS = {
    _GLB_SERIALIZER_CPP: "C++ (adacpp native)",
    _GLB_SERIALIZER_PYTHON: "Python (ada)",
    _GLB_SERIALIZER_WASM: "WASM (browser)",
}


# Serializers that execute in the browser — the SPA routes these to its
# in-browser pipeline and gates them on client capability (wasmSupport).
_GLB_CLIENT_SERIALIZERS = frozenset({_GLB_SERIALIZER_WASM})


# The two in-browser engines the `wasm` serializer offers, most-preferred first. adapy's OWN
# vocabulary, unlike the python serializer's (discovered from adacpp): these name browser pipelines
# — the adacpp embind module and the Pyodide wheel stack — that no adacpp build reports and no
# worker runs. The SPA matches "wasm-native" to pick the embind pipeline, so this tuple and
# services/conversion/index.ts share one contract; test_wasm_engine_tokens_are_the_spa_contract
# pins it.
_WASM_ENGINE_NATIVE = "wasm-native"


_WASM_ENGINE_PYODIDE = "pyodide"


_WASM_GLB_ENGINES = (_WASM_ENGINE_NATIVE, _WASM_ENGINE_PYODIDE)


# Labels adapy OWNS: its own serializer-pinned token and the two in-browser engines, none of which
# adacpp knows about. Every adacpp track's label + description comes from the track itself
# (_glb_tess_label / _glb_tess_description), so a new track arrives fully described.
_GLB_TESS_NATIVE_PINNED = "native"


_GLB_TESS_OWN_LABELS = {
    # The cpp serializer's fallback token, used only against an adacpp whose native binding can't
    # take a track (< 0.16). Newer builds advertise the discovered adacpp:* tracks instead, so this
    # stays resolvable for stored configs rather than naming a choice we still offer.
    _GLB_TESS_NATIVE_PINNED: "Native (libtess2)",
    # client-side (wasm serializer) engines:
    "wasm-native": "Native (WASM module)",
    "pyodide": "Pyodide (wasm wheels)",
    # legacy tokens, still resolvable for stored configs / older SPA builds:
    "pyocc": "PythonOCC (OCC)",
    "adacpp-occ": "adacpp OCC",
    "cgal": "adacpp CGAL",
    "ifc-hybrid": "ifcOpenShell hybrid",
}


def _glb_tess_label(tok: str) -> str:
    """Human label for a tessellator token. Discovered tracks describe themselves."""
    from ada.cad.registry import tess_track_by_name

    track = tess_track_by_name(_LEGACY_TESS_ALIASES.get(tok, tok))
    if track is not None:
        return track.label
    return _GLB_TESS_OWN_LABELS.get(tok, tok)


def _glb_tess_description(tok: str) -> str:
    """One-line tooltip for a tessellator token, from the track's own declaration ('' if it has
    none). This is why a track can explain its own cost/benefit in the UI without adapy knowing
    anything about it."""
    from ada.cad.registry import tess_track_by_name

    track = tess_track_by_name(_LEGACY_TESS_ALIASES.get(tok, tok))
    return track.description if track is not None else ""


def _python_tess_tokens() -> list[str]:
    """The `python` serializer's tessellator tokens — DISCOVERED from adacpp, not enumerated here.

    Adding a track in adacpp makes it appear in this dropdown with no change in adapy. The order puts
    the declared default first (the dropdown's default is enum_by[...][0]).

    Takes no source family ON PURPOSE. The pre-discovery table split step/generic, but only to list
    the duplicate `occ` alias on STEP (see _LEGACY_TESS_ALIASES) — every real kernel was offered to
    both. Each track reaches both families: STEP via `step_glb_pipeline`, everything else via
    `glb_tess_engine` -> ADA_STREAM_TESS_PIPELINE, and adapy's own OCC track is the BatchTessellator
    default on generic. So there is no family split left to honour, and a `fam` argument here would
    be a lie about a distinction that no longer exists.
    """
    from ada.cad.registry import available_tess_tracks

    tracks = available_tess_tracks()
    ranked = sorted(tracks, key=lambda t: (not t.is_default, t.backend.value != "adacpp", t.name))
    return [t.name for t in ranked]


def _cpp_tess_tokens() -> list[str]:
    """The `cpp` serializer's tessellator tokens — the same adacpp tracks the `python` serializer
    discovers, because both dispatch to the same kernel; the native path just reaches it through
    the C++ reader instead of the NGEOM wire.

    Two gates, both asked rather than assumed. The binding must accept a track at all (adacpp < 0.16
    ignores ``pipeline`` and runs libtess2), and the track must be one adacpp declares NEUTRAL — the
    taxonomy kernels need ifcopenshell geometry the C++ STEP reader never builds, and on this path
    they mesh as if nothing were selected instead of erroring. Gating the ADVERTISEMENT on the same
    facts the conversion checks is the point: an offer we can't honour reports a track we never ran.
    """
    # From the registry, NOT ada.cadit: this runs at module import to build the published
    # vocabulary, and the slim viewer image ships ada/cad but no ada/cadit — importing the latter
    # here crashlooped the API on startup. The registry answers False with no adacpp, which is the
    # right answer for the API: it runs no conversions, and the pools that do advertise their own.
    from ada.cad.registry import (
        CadBackendName,
        native_track_selection_available,
        tess_track_by_name,
    )

    if not native_track_selection_available():
        return [_GLB_TESS_NATIVE_PINNED]
    tracks = []
    for tok in _python_tess_tokens():
        t = tess_track_by_name(tok)
        if t is not None and t.backend is CadBackendName.ADACPP and t.neutral:
            tracks.append(tok)
    return tracks or [_GLB_TESS_NATIVE_PINNED]


def _face_regions_serializers(fam: str, serializers: list[str]) -> list[str]:
    """The serializers that can actually EMIT per-face regions for this source family.

    * ``cpp`` (both native whole-file converters): they feed the same neutral tessellator (which
      captures the ranges) and the same C++ GLB writer (which emits face_ranges_node). Until adacpp
      0.16 stream_ifc_to_glb simply didn't forward the flag, so the check is per-family against the
      binding that would run.
    * ``python`` (STEP only): the dedicated OCC-clickable track
      (``OccBackend.step_to_face_tagged_meshes`` -> ``convert_step_to_occ_clickable_glb``). It
      OCC-tessellates each face and writes ``face_ranges_node`` itself, so — unlike the generic
      python →GLB path — it does NOT depend on the NGEOM wire carrying the ranges. It's the way to
      get clickable faces on OCC tessellation (e.g. rational-B-spline surfaces the native
      tessellator can mishandle). Gated on the OCC backend being importable in this pool.

    Asks the REGISTRY, not ada.cadit: this runs at module import to build the published vocabulary,
    and the slim viewer image ships ada/cad but no ada/cadit.
    """
    from ada.cad.registry import (
        CadBackendName,
        backend_available,
        native_face_regions_available,
    )

    out: list[str] = []
    if _GLB_SERIALIZER_CPP in serializers and native_face_regions_available(fam):
        out.append(_GLB_SERIALIZER_CPP)
    if fam == "step" and _GLB_SERIALIZER_PYTHON in serializers and backend_available(CadBackendName.OCC):
        out.append(_GLB_SERIALIZER_PYTHON)
    return out


def _glb_serializer_tess(serializer: str) -> list[str] | None:
    """Tessellator tokens for a serializer, or None if it isn't offered.

    `wasm` runs in-browser engines adacpp doesn't know about, so it stays declared here — it is
    adapy's own. `python` and `cpp` both dispatch to adacpp and so both DISCOVER their tracks;
    `cpp` degrades to its historic pinned token against an adacpp whose binding can't select one.
    """
    if serializer == _GLB_SERIALIZER_CPP:
        return _cpp_tess_tokens()
    if serializer == _GLB_SERIALIZER_WASM:
        return list(_WASM_GLB_ENGINES)
    if serializer == _GLB_SERIALIZER_PYTHON:
        return _python_tess_tokens()
    return None


# Serializer ordering per family (default is the first entry). cpp is the
# default server path for both; the client serializer comes last.
_GLB_SERIALIZER_ORDER = (
    _GLB_SERIALIZER_CPP,
    _GLB_SERIALIZER_PYTHON,
    _GLB_SERIALIZER_WASM,
)


def _glb_source_family(source_ext: str) -> str:
    """'step' for STEP/STP sources, else 'generic' — selects the serializer
    tessellator vocabulary + engine-knob axis."""
    return "step" if source_ext.lower().lstrip(".") in ("step", "stp") else "generic"


def _glb_serializer_options(source_ext: str) -> list[dict]:
    """Build the ``serializer`` + dependent ``tessellator`` enum options for a
    →GLB row. Single source: vocabulary, labels, defaults and the
    serializer→tessellator dependency all come from the module spec above, so
    the frontend can render the two dependent dropdowns without hardcoding any
    of it. ``enum_by`` maps each serializer value to its allowed tessellator
    tokens; ``runtime='client'`` flags the browser-side serializers.

    ``source_ext`` no longer selects the tessellator vocabulary (every kernel reaches every source
    family — see :func:`_python_tess_tokens`); it is kept because it names the row this schema is
    attached to and because the resolver still splits on family to pick the ENGINE KNOB.
    """
    fam = _glb_source_family(source_ext)
    serializers = [s for s in _GLB_SERIALIZER_ORDER if _glb_serializer_tess(s)]
    enum_by = {s: list(_glb_serializer_tess(s)) for s in serializers}
    # Union of tessellator tokens across serializers, ordered by first appearance.
    tess_tokens: list[str] = []
    for s in serializers:
        for tok in enum_by[s]:
            if tok not in tess_tokens:
                tess_tokens.append(tok)
    default_ser = serializers[0]
    return [
        {
            "name": "serializer",
            "type": "enum",
            "title": "Serializer",
            "default": default_ser,
            "enum": serializers,
            "labels": {s: _GLB_SERIALIZER_LABELS[s] for s in serializers},
            "runtime": {s: ("client" if s in _GLB_CLIENT_SERIALIZERS else "server") for s in serializers},
            "description": (
                "Conversion code path for →GLB. 'cpp' = pure-C++ adacpp (fastest, lowest memory); "
                "'python' = Python-orchestrated (lets you pick the tessellation kernel below); "
                "'wasm' / 'pyodide' run entirely in your browser (no server round-trip)."
            ),
        },
        {
            "name": "tessellator",
            "type": "enum",
            # Mesh target → this axis meshes the geometry ("Tessellator"). (For B-rep targets the
            # analogous axis is titled "Writer" — same mechanism, set on those rows.)
            "title": "Tessellator",
            "default": enum_by[default_ser][0],
            "enum": tess_tokens,
            "labels": {t: _glb_tess_label(t) for t in tess_tokens},
            # Per-token tooltips, sourced from each track's own declaration. Empty for adapy's own
            # tokens, which the frontend renders without a tooltip.
            "descriptions": {t: _glb_tess_description(t) for t in tess_tokens if _glb_tess_description(t)},
            # Dependent dropdown: the valid tessellators for each serializer. The
            # frontend repopulates + reselects when the serializer changes.
            "enum_by": enum_by,
            "depends_on": "serializer",
            "description": (
                "Tessellation kernel. 'python' and 'cpp' both offer the tracks adacpp declares "
                "(cpp only the ones its native reader can drive); 'wasm' pins its in-browser "
                "engines."
            ),
        },
        {
            "name": "face_regions",
            "type": "bool",
            "title": "Clickable surfaces",
            "default": False,
            "depends_on": "serializer",
            # The NATIVE paths emit per-face regions — STEP and IFC both forward face_regions to
            # adacpp, which writes face_ranges_node into the GLB's scene extras. The python path
            # never reads the flag, so advertising it there would offer a capability that silently
            # doesn't happen. Empty list = offered nowhere on this row, which the SPA renders as a
            # disabled toggle rather than a lie.
            "supported_by": _face_regions_serializers(fam, serializers),
            "description": (
                "Embed per-face pick regions (the source's face ids) in the GLB so individual "
                "surfaces can be clicked. A debugging aid: it enlarges the GLB and forces serial "
                "face tessellation, so it is off by default. Only FACE-SET geometry has faces to "
                "mark up — a model built from swept or CSG solids converts fine but has no regions "
                "to click."
            ),
        },
    ]


# Option-dict keys that are per-value MAPPINGS rather than scalars: each key is an enum value (or,
# for enum_by, a value of the option it depends_on). Two pools advertising the same option each
# carry entries only for the values THEY advertise, so these merge key-by-key.
_OPTION_MAP_KEYS = ("labels", "descriptions", "runtime")


# Option-dict keys that are LISTS of values a pool can honour. Each pool advertises only what IT
# can run, so the cluster's answer is the union — first-writer-wins would let a pool that lacks a
# capability pin its absence for everyone (the same way a thin pool's enum_by once pinned the whole
# cluster's tessellator list).
_OPTION_LIST_KEYS = ("enum", "supported_by")


def merge_option_into(cur: dict, incoming: dict) -> None:
    """Union one worker's advertisement of an option into the accumulated one, in place.

    Lives here, next to the code that BUILDS these dicts (_glb_serializer_options), because how two
    advertisements of a schema combine is a fact about the schema.

    First-writer-wins on everything but ``enum`` — which is what the API's merge used to be —
    silently loses data as soon as two pools differ. A thin pool advertising
    ``enum_by={'python': ['occ']}`` pinned that for the whole cluster, while the adacpp pool's
    tracks, already unioned into ``enum``, reached the dropdown with no serializer offering them:
    rendered and unselectable. The per-value maps fail the same way — whichever pool registered
    first decides, and tokens only the other pool knows arrive unlabelled.

    So: union the per-pool lists (``enum`` / ``supported_by``) and each ``enum_by`` list, fill in
    the per-value maps, and leave scalars (``default``/``title``/``description``/``type``/
    ``depends_on``) to the first writer — those describe the option itself rather than its values,
    and every pool derives them from this module.
    """
    import copy

    for key in _OPTION_LIST_KEYS:
        if isinstance(incoming.get(key), list) and isinstance(cur.get(key), list):
            cur[key] = cur[key] + [v for v in incoming[key] if v not in cur[key]]
        elif isinstance(incoming.get(key), list) and cur.get(key) is None:
            cur[key] = list(incoming[key])
    if isinstance(incoming.get("enum_by"), dict):
        by = cur.get("enum_by")
        if not isinstance(by, dict):
            cur["enum_by"] = copy.deepcopy(incoming["enum_by"])
        else:
            for key, vals in incoming["enum_by"].items():
                if not isinstance(vals, list):
                    continue
                have = by.get(key)
                by[key] = have + [v for v in vals if v not in have] if isinstance(have, list) else list(vals)
    for key in _OPTION_MAP_KEYS:
        if not isinstance(incoming.get(key), dict):
            continue
        have = cur.get(key)
        if isinstance(have, dict):
            # setdefault, not update: a pool contributes descriptions for the values it knows and
            # can never blank out another pool's.
            for k, v in incoming[key].items():
                have.setdefault(k, v)
        else:
            cur[key] = dict(incoming[key])


def _apply_glb_serializer(
    source_ext: str,
    serializer: str | None,
    tessellator: str | None,
    *,
    step_glb_pipeline: str | None,
    glb_tess_engine: str | None,
) -> tuple[str | None, str | None, bool]:
    """Fold ``serializer`` / ``tessellator`` tokens into the effective engine
    knobs. Returns ``(step_glb_pipeline, glb_tess_engine, force_python)``.

    ``force_python`` tells IFC→GLB to bypass the pure-native path and route
    through the ifcopenshell import + BatchTessellator (so the chosen kernel
    actually takes effect). When ``serializer`` is unset the explicit knobs
    (and their env/defaults) are returned untouched — full back-compat with the
    admin panel + ``ADAPY_*`` env selection. Client serializers never reach a
    worker, so they are treated as no-ops here."""
    if not serializer or serializer in _GLB_CLIENT_SERIALIZERS:
        return step_glb_pipeline, glb_tess_engine, False
    fam = _glb_source_family(source_ext)
    if serializer == _GLB_SERIALIZER_CPP:
        if fam == "step":
            step_glb_pipeline = _STEP_GLB_PIPELINE_ADACPP_NATIVE
        # The track rides the tess-engine axis for BOTH families: `step_glb_pipeline` is already
        # spent naming the native CODE PATH, and the two are independent axes (which reader vs
        # which kernel), so folding the track into that token would conflate them. `adacpp-native`
        # resolves to no CadConfig, so nothing else reads glb_tess_engine on this branch;
        # generic/IFC 'cpp' is native_ifc_to_glb, which reverses it via _native_track_for_engine.
        # Dropping it here (as the generic branch used to) meant a track picked for an IFC source
        # silently ran the default while the UI reported the choice.
        if tessellator and tessellator != _GLB_TESS_NATIVE_PINNED:
            glb_tess_engine = _tess_token_to_glb_engine(tessellator)
        return step_glb_pipeline, glb_tess_engine, False
    if serializer == _GLB_SERIALIZER_PYTHON:
        tok = tessellator or _glb_serializer_tess(_GLB_SERIALIZER_PYTHON)[0]
        if fam == "step":
            step_glb_pipeline = _tess_token_to_pipeline(tok)
        else:
            glb_tess_engine = _tess_token_to_glb_engine(tok)
        return step_glb_pipeline, glb_tess_engine, True
    return step_glb_pipeline, glb_tess_engine, False


# ---------------------------------------------------------------------------
# B-rep → B-rep (step→ifc, ifc→step) path options. Same shared frontend selector
# as the →GLB rows, but the second axis is a WRITER (no tessellation happens),
# titled "Writer" via the backend. The writer mirrors the serializer 1:1 — each
# code path has exactly one B-rep emitter — so the writer dropdown is
# informational (one entry per serializer), consistent with how cpp→glb pins its
# tessellator. Client (wasm) B-rep writers run in-browser via the adacpp wasm
# writer modules.
_BREP_SERIALIZER_LABELS = {
    _GLB_SERIALIZER_CPP: "C++ (adacpp native)",
    _GLB_SERIALIZER_PYTHON: "Python (OCC)",
    _GLB_SERIALIZER_WASM: "WASM (browser)",
}


# serializer -> (writer token, writer label). One writer per path.
_BREP_SERIALIZER_WRITER = {
    _GLB_SERIALIZER_CPP: ("native", "Native (adacpp B-rep)"),
    _GLB_SERIALIZER_PYTHON: ("occ", "OCC / ifcopenshell"),
    _GLB_SERIALIZER_WASM: ("wasm-native", "Native (WASM)"),
}


_BREP_SERIALIZER_ORDER = (_GLB_SERIALIZER_CPP, _GLB_SERIALIZER_PYTHON, _GLB_SERIALIZER_WASM)


def _brep_serializer_options(source_ext: str, target: str) -> list[dict]:
    """serializer + dependent ``writer`` options for a B-rep→B-rep row (single-sourced, like
    :func:`_glb_serializer_options`). The writer axis is titled "Writer" and mirrors the serializer."""
    serializers = list(_BREP_SERIALIZER_ORDER)
    enum_by = {s: [_BREP_SERIALIZER_WRITER[s][0]] for s in serializers}
    writer_tokens: list[str] = []
    for s in serializers:
        for tok in enum_by[s]:
            if tok not in writer_tokens:
                writer_tokens.append(tok)
    writer_labels = {_BREP_SERIALIZER_WRITER[s][0]: _BREP_SERIALIZER_WRITER[s][1] for s in serializers}
    default_ser = serializers[0]
    return [
        {
            "name": "serializer",
            "type": "enum",
            "title": "Serializer",
            "default": default_ser,
            "enum": serializers,
            "labels": {s: _BREP_SERIALIZER_LABELS[s] for s in serializers},
            "runtime": {s: ("client" if s in _GLB_CLIENT_SERIALIZERS else "server") for s in serializers},
            "description": (
                f"Conversion code path for →{target.upper()}. 'cpp' = pure-C++ adacpp B-rep writer "
                "(fastest, dep-free); 'python' = the OpenCASCADE / ifcopenshell writer; 'wasm' runs "
                "the adacpp writer entirely in your browser (no server round-trip)."
            ),
        },
        {
            "name": "tessellator",  # wire key kept stable across targets; DISPLAYED as "Writer"
            "type": "enum",
            "title": "Writer",
            "default": enum_by[default_ser][0],
            "enum": writer_tokens,
            "labels": writer_labels,
            "enum_by": enum_by,
            "depends_on": "serializer",
            "description": "B-rep writer. Mirrors the serializer — each code path has one writer.",
        },
    ]


def _conversion_path_options(source_ext: str, target: str) -> list[dict]:
    """The shared serializer × (tessellator|writer) path options for a (source, target) row. Mesh
    targets (glb) get the tessellator axis; B-rep targets (ifc/step) get the writer axis. Single
    source of the vocabulary for the frontend selector."""
    t = target.lstrip(".").lower()
    if t == "glb":
        return _glb_serializer_options(source_ext)
    if t in ("ifc", "step"):
        return _brep_serializer_options(source_ext, t)
    return []


def _brep_writer_is_python(serializer: str | None) -> bool:
    """A B-rep→B-rep serializer that routes to the Python/OCC writer (vs the native adacpp emitter).
    Client (wasm) serializers never reach the worker, so they are not 'python' here."""
    return serializer == _GLB_SERIALIZER_PYTHON
