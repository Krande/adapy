"""STEP→GLB pipeline and →GLB tessellation-engine vocabulary: what exists, what this process can
run, defaults and the resolution to a CadBackend config.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass


# STEP→GLB engines. ``adacpp-native`` (the fully in-process C++ reader+tessellate+write, validated
# 1:1 with the Python path) is the default; it falls back to ``libtess2`` (adacpp's OCC-free boundary
# CDT, step2glb-parity geometry incl. curved surfaces the OCC stream reader drops) and then the
# ``occ-builtin`` OCC streaming reader. ``adacpp-{occ,cgal,hybrid}`` route through adacpp's linked
# OCCT / ifcopenshell-taxonomy kernels (extra options). (The external ``step2glb`` binary engine was
# removed — libtess2 reaches the same geometry in-process, so the unprovisioned binary path is gone.)
_STEP_GLB_PIPELINE_LIBTESS2 = "libtess2"


_STEP_GLB_PIPELINE_OCC = "occ-builtin"


_STEP_GLB_PIPELINE_ADACPP_OCC = "adacpp-occ"


_STEP_GLB_PIPELINE_ADACPP_CGAL = "adacpp-cgal"


_STEP_GLB_PIPELINE_ADACPP_HYBRID = "adacpp-hybrid"


# Fully-native: adacpp does the whole STEP->GLB in-process (C++ reader + thread pool + GLB writer),
# replacing the Python reader + multiprocess pool. Fastest + lowest memory, and now byte-faithful to
# the Python path — geometry, product names, per-instance picking, and the full assembly tree are
# validated 1:1 on the large reference assembly (see native_step_to_glb / validate_native_vs_python.py). This is the
# default; it degrades gracefully to libtess2 if adacpp's native entry point is missing or the
# conversion raises.
_STEP_GLB_PIPELINE_ADACPP_NATIVE = "adacpp-native"


# The four historic (adacpp track pipeline <-> `step_glb_pipeline` token) identities. Pinned in
# BOTH directions from one dict so the two mappings cannot drift; everything else is structural
# (``adacpp:X`` <-> ``adacpp-X``), which is what lets a track added later in adacpp resolve
# end-to-end without an edit here.
_HISTORIC_TRACK_PIPELINE = {
    "libtess2": _STEP_GLB_PIPELINE_LIBTESS2,
    "occ": _STEP_GLB_PIPELINE_ADACPP_OCC,
    "cgal": _STEP_GLB_PIPELINE_ADACPP_CGAL,
    "hybrid": _STEP_GLB_PIPELINE_ADACPP_HYBRID,
}


_HISTORIC_PIPELINE_TRACK = {v: f"adacpp:{k}" for k, v in _HISTORIC_TRACK_PIPELINE.items()}


def _pipeline_to_track_name(pipe: str) -> str | None:
    """`step_glb_pipeline` token -> adacpp track name, or None for the paths that aren't a track
    (occ-builtin, adacpp-native).

    VOCABULARY, not capability: this says what a token *means*, never whether this process can run
    it. Availability is checked one layer up, in :func:`_cad_config_for_pipeline`, which warns and
    degrades. Keeping the two apart is what makes the answer identical in the API, the worker and
    the test envs — see :func:`_tess_token_to_pipeline` for what the conflation cost.
    """
    if pipe in _HISTORIC_PIPELINE_TRACK:
        return _HISTORIC_PIPELINE_TRACK[pipe]
    if pipe.startswith("adacpp-") and pipe != _STEP_GLB_PIPELINE_ADACPP_NATIVE:
        return f"adacpp:{pipe[len('adacpp-'):]}"
    return None  # occ-builtin / adacpp-native / unknown → not a stream CadConfig


def _adacpp_stream_pipelines() -> tuple[str, ...]:
    """Every `step_glb_pipeline` token backed by a DISCOVERED adacpp track (adacpp-native excluded:
    it is a whole-file code path, not a tessellator)."""
    from ada.cad.registry import CadBackendName, available_tess_tracks

    out = []
    for t in available_tess_tracks():
        if t.backend is not CadBackendName.ADACPP:
            continue
        tok = _tess_token_to_pipeline(t.name)
        if tok not in out:
            out.append(tok)
    return tuple(out)


_STEP_GLB_PIPELINES_STATIC = (
    _STEP_GLB_PIPELINE_LIBTESS2,
    _STEP_GLB_PIPELINE_OCC,
    _STEP_GLB_PIPELINE_ADACPP_OCC,
    _STEP_GLB_PIPELINE_ADACPP_CGAL,
    _STEP_GLB_PIPELINE_ADACPP_HYBRID,
    _STEP_GLB_PIPELINE_ADACPP_NATIVE,
)


def _step_glb_pipelines() -> tuple[str, ...]:
    """The accepted `step_glb_pipeline` vocabulary: adapy's own tokens + every discovered adacpp
    track. Derived, so a new track is accepted without touching this module."""
    out = list(_STEP_GLB_PIPELINES_STATIC)
    for tok in _adacpp_stream_pipelines():
        if tok not in out:
            out.append(tok)
    return tuple(out)


_STEP_GLB_PIPELINE_DEFAULT = _STEP_GLB_PIPELINE_ADACPP_NATIVE


# Where the native path degrades to when adacpp is absent or a conversion raises. Kept separate from
# the default so the native branch's fallback is never circular.
_STEP_GLB_PIPELINE_FALLBACK = _STEP_GLB_PIPELINE_LIBTESS2


def available_step_glb_pipelines() -> tuple[str, ...]:
    """The STEP→GLB engines THIS process can actually run — for per-worker capability advertisement.

    A worker pool without adacpp won't advertise the adacpp engines; one without OCC won't advertise
    occ-builtin. The API unions these across pools for the engine list, and routes a job requesting an
    engine to a pool that advertises it. Detection is conservative: if nothing is detected (which
    shouldn't happen in a real worker) it returns the full set rather than an empty one.
    """
    import importlib.util

    def _have(mod: str) -> bool:
        try:
            return importlib.util.find_spec(mod) is not None
        except Exception:
            return False

    runnable: set[str] = set()
    if _have("adacpp"):
        # adacpp-native is gated with the rest of the adacpp engines (find_spec presence) rather than an
        # import-based native_adacpp_available() check: under the deployed adacpp-overlay, import caching
        # can resolve a base adacpp (without the native entrypoint) before the overlay path is active, so
        # the import check spuriously reports False at worker-startup advert time. find_spec is resolved
        # at call time against sys.path and isn't poisoned by an earlier import; the worker's runtime
        # fallback (native → libtess2 → occ-builtin) still covers a pool that turns out not to run it.
        # Discovered, not listed: adacpp reports which tracks IT compiled, so a build without the
        # taxonomy kernels (or with a new one) advertises the truth rather than this module's guess.
        runnable.update(_adacpp_stream_pipelines())
        runnable.add(_STEP_GLB_PIPELINE_ADACPP_NATIVE)  # a whole-file code path, not a track
    if _have("OCP") or _have("OCC"):
        runnable.add(_STEP_GLB_PIPELINE_OCC)
    avail = tuple(p for p in _step_glb_pipelines() if p in runnable)
    return avail or _step_glb_pipelines()


def available_tess_tokens() -> frozenset[str]:
    """The `tessellator` tokens THIS process can actually run — the serializer×tessellator analogue
    of :func:`available_step_glb_pipelines`, for per-worker capability advertisement.

    Every discovered track name (``occ``, ``adacpp:libtess2``, ``adacpp:cdt``, ...) plus the ``cpp``
    serializer's pinned ``native`` token, which is adacpp's in-process writer and so rides on adacpp
    being importable.

    Client tokens (``wasm-native`` / ``pyodide``) are deliberately ABSENT: they run in the browser,
    so no server-side probe can answer for them and they must never be gated on what a worker has
    installed — see ``worker._gate_advertised_engines``.
    """
    from ada.cad.registry import (
        CadBackendName,
        available_tess_tracks,
        backend_available,
    )

    toks = {t.name for t in available_tess_tracks()}
    if backend_available(CadBackendName.ADACPP):
        toks.add("native")
    return frozenset(toks)


# Non-STEP →GLB engine toggle (xml / ifc / sat / fem / obj / stl → glb via to_gltf's
# BatchTessellator). Reuses the STEP option's names so the admin panel reads consistently, but maps
# to the BatchTessellator stream selector (ADA_STREAM_TESS_PIPELINE); "occ-builtin" = the default
# OCC BatchTessellator (no stream override).
_GLB_TESS_ENGINES_STATIC = (
    _STEP_GLB_PIPELINE_OCC,  # "occ-builtin"
    _STEP_GLB_PIPELINE_LIBTESS2,
    _STEP_GLB_PIPELINE_ADACPP_OCC,
    _STEP_GLB_PIPELINE_ADACPP_CGAL,
    _STEP_GLB_PIPELINE_ADACPP_HYBRID,
)


def _glb_tess_engines() -> tuple[str, ...]:
    """The `glb_tess_engine` vocabulary: adapy's own tokens + every discovered adacpp track.

    ``adacpp-native`` is excluded — it is a whole-file STEP code path, not a BatchTessellator
    kernel. Derived rather than listed for the same reason as :func:`_step_glb_pipelines`: the tuple
    this replaced could not grow a track added later in adacpp.

    Shaped like _step_glb_pipelines() — a static base that discovery only ADDS to — so the answer
    is the same in every process. Returning just what adacpp reports would make the VOCABULARY
    env-dependent (an adacpp-less API would stop knowing the word "adacpp-cgal" rather than knowing
    it and not offering it), which is the conflation _tess_token_to_pipeline exists to avoid.
    Capability is applied by ``worker._gate_advertised_engines``.
    """
    out = list(_GLB_TESS_ENGINES_STATIC)
    for tok in _adacpp_stream_pipelines():
        if tok not in out:
            out.append(tok)
    return tuple(out)


# Advertised default for the non-STEP →GLB engine option. Kept in lockstep with the RUNTIME default
# (`_default_glb_tess_engine`, which returns libtess2 whenever adacpp is importable) so the SPA/audit
# UI reflects the path actually taken. libtess2 gracefully degrades to OCC on an adacpp-less pool.
_GLB_TESS_ENGINE_DEFAULT = _STEP_GLB_PIPELINE_LIBTESS2


def _glb_engine_to_stream(engine: str) -> str | None:
    """`glb_tess_engine` token -> the ``ADA_STREAM_TESS_PIPELINE`` value, or None for the OCC
    BatchTessellator default (``occ-builtin`` / unknown).

    Derived from the engine token, not a table: the table this replaced listed exactly the four
    engines that existed when it was written, so a track discovered later (``adacpp-cdt``) missed
    it, resolved to None, and SILENTLY ran the OCC BatchTessellator — the user picked a kernel and
    got a different one, with nothing logged.
    """
    track = _pipeline_to_track_name(engine)
    return None if track is None else track.split(":", 1)[1]


def _default_glb_tess_engine() -> str:
    """Default engine for the non-STEP →GLB (scene) path: ``libtess2`` when adacpp is importable,
    else the OCC BatchTessellator. OCC's prism tessellation of curved B-spline plates is
    NON-MANIFOLD — it drops the viewer's per-plate edge outlines (hull-skin plates) — so
    libtess2 (manifold; non-NGEOM-serializable geom still falls back to OCC per-object) is
    preferred wherever it can run. Evaluated at conversion time so a slim/adacpp-less pool still
    gets OCC."""
    from importlib.util import find_spec

    try:
        if find_spec("adacpp") is not None:
            return _STEP_GLB_PIPELINE_LIBTESS2
    except Exception:  # noqa: BLE001 - find_spec can raise on a broken import path
        pass
    return _STEP_GLB_PIPELINE_OCC


def _glb_engine_stream_value(engine: str | None) -> str | None:
    """Map the non-STEP →GLB engine option to a BatchTessellator stream pipeline value
    (``ADA_STREAM_TESS_PIPELINE``), or ``None`` for the default OCC BatchTessellator
    (``occ-builtin`` / unknown). Per-job ``engine`` wins, else the global ``ADAPY_GLB_TESS_ENGINE``
    env (set by the worker from the per-source-type ``tess_engine_*`` setting), else the
    adacpp-aware default (``_default_glb_tess_engine``)."""
    import os

    choice = (engine or os.environ.get("ADAPY_GLB_TESS_ENGINE", "") or _default_glb_tess_engine()).strip().lower()
    return _glb_engine_to_stream(choice)


def _resolve_step_glb_pipeline(step_glb_pipeline: str | None) -> str:
    """Pick the STEP→GLB engine.

    Precedence mirrors ``_should_stream_step``: an explicit per-job choice
    (``step_glb_pipeline`` kwarg, set by the worker from the job option) wins;
    otherwise the global ``ADAPY_STEP_GLB_PIPELINE`` env (same convention as
    ``ADAPY_CAD_BACKEND``); default ``adacpp-native``. The native path degrades
    to ``libtess2`` (then ``occ-builtin``) when adacpp is missing or a conversion
    raises, so the native default is safe everywhere.
    """
    import os

    from ada.config import logger

    choice = (
        (step_glb_pipeline or os.environ.get("ADAPY_STEP_GLB_PIPELINE", "") or _STEP_GLB_PIPELINE_DEFAULT)
        .strip()
        .lower()
    )
    if choice not in _step_glb_pipelines():
        logger.warning("unknown ADAPY_STEP_GLB_PIPELINE %r; falling back to %s", choice, _STEP_GLB_PIPELINE_DEFAULT)
        return _STEP_GLB_PIPELINE_DEFAULT
    return choice


def _step_glb_fallback_chain(pipe: str, cad_cfg):
    """Ordered (pipeline, CadConfig) attempts for an adacpp STEP→GLB pipeline that yields
    nothing. The requested pipeline first, then adacpp's own linked-OCCT kernel
    (``adacpp:occ``) — the right fallback when we started on the OCC-free libtess2 path
    (no pythonocc TopoDS to begin with) and on wasm, where pythonocc isn't available at all.
    The pythonocc ``occ-builtin`` path is tried last (native only), by the caller falling
    through. De-duplicated so we never retry the same pipeline."""
    chain = [(pipe, cad_cfg)]
    occ_cfg = _cad_config_for_pipeline(_STEP_GLB_PIPELINE_ADACPP_OCC)
    if occ_cfg is not None and pipe != _STEP_GLB_PIPELINE_ADACPP_OCC:
        chain.append((_STEP_GLB_PIPELINE_ADACPP_OCC, occ_cfg))
    return chain


def _cad_config_for_pipeline(pipe: str):
    """Map a STEP→GLB pipeline to a ``CadConfig`` for the streaming converter, or
    ``None`` for the OCC-builtin path (and as a graceful fallback when the requested
    adacpp tessellation path isn't available in this environment)."""
    from ada.cad.registry import CadConfig, tess_track_by_name
    from ada.config import logger

    name = _pipeline_to_track_name(pipe)
    if name is None:
        return None  # occ-builtin → OCC streaming default
    if tess_track_by_name(name) is None:
        logger.warning("step-glb pipeline %r unavailable in this environment; using occ-builtin", pipe)
        return None
    return CadConfig(path=name)


# LEGACY tessellator tokens -> track name. The python serializer's vocabulary used to be a
# hand-written list; it is now DISCOVERED (see _python_tess_tokens), which is the only reason
# `adacpp:cdt` can reach the UI at all — a hand-written list cannot grow a track added later in
# adacpp. These aliases keep stored configs, the admin panel and older SPA builds resolving.
#
# `pyocc` and `occ` BOTH land on adapy's own OCC track, because they always were the same engine:
# the pre-discovery table mapped both to `occ-builtin` and merely labelled them differently
# ("PythonOCC (OCC)" vs "OCC streaming reader"), so the STEP dropdown listed one engine twice.
_LEGACY_TESS_ALIASES = {
    "native": "adacpp:libtess2",
    "pyocc": "occ",
    "occ": "occ",
    "adacpp-occ": "adacpp:occ",
    "cgal": "adacpp:cgal",
    "ifc-hybrid": "adacpp:hybrid",
}


# tessellator token → effective engine knob, reusing the existing pipeline
# identities so there is exactly one definition of each engine.
def _tess_token_to_pipeline(tok: str) -> str:
    """Tessellator token -> the `step_glb_pipeline` knob. Resolves legacy tokens and any track name,
    including ones that postdate this module (e.g. ``adacpp:cdt`` -> ``adacpp-cdt``).

    VOCABULARY, not capability — deliberately: this used to resolve the token by LOOKING THE TRACK
    UP in ``available_tess_tracks()`` and falling back to libtess2 when it wasn't found, which made
    the same stored token mean different engines in different processes (``cgal`` -> ``adacpp-cgal``
    where adacpp is importable, ``libtess2`` where it isn't). Worse, the fallback was itself an
    adacpp engine, so it could not be what an adacpp-less pool meant. Resolution is now purely
    structural and identical everywhere; whether the resolved engine can actually RUN is decided by
    ``available_step_glb_pipelines`` / ``_gate_advertised_engines`` / ``_cad_config_for_pipeline``.
    """
    name = _LEGACY_TESS_ALIASES.get(tok, tok)
    if name == "occ":
        return _STEP_GLB_PIPELINE_OCC  # adapy's own BRepMesh (no stream override)
    if not name.startswith("adacpp:"):
        return _STEP_GLB_PIPELINE_LIBTESS2  # unknown token → the OCC-free default
    return _HISTORIC_TRACK_PIPELINE.get(name.split(":", 1)[1], f"adacpp-{name.split(':', 1)[1]}")


def _tess_token_to_glb_engine(tok: str) -> str:
    """Tessellator token -> the `glb_tess_engine` knob (the non-STEP BatchTessellator selector).
    Same structural resolution as _tess_token_to_pipeline."""
    return _tess_token_to_pipeline(tok)


def _native_track_for_engine(glb_tess_engine: str | None) -> str | None:
    """The adacpp `pipeline` name for the native STEP->GLB path, or None to leave adacpp's default.

    The cpp serializer parks its chosen track on the tess-engine axis (see _apply_glb_serializer),
    so this reverses that: 'adacpp-cdt' -> 'cdt', the token adacpp's own binding takes. VOCABULARY,
    not capability — native_step_to_glb re-checks its binding and refuses rather than substituting.
    Engines that aren't an adacpp track (occ-builtin, adacpp-native itself) yield None.
    """
    if not glb_tess_engine:
        return None
    track_name = _pipeline_to_track_name(glb_tess_engine)
    if not track_name:
        return None
    return track_name.split(":", 1)[1] or None
