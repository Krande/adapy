"""CAD backend + tessellation-path registry and config.

Lets a user discover which CAD backends and tessellation paths are available in the current
environment (adacpp present => its extra paths are listed) and select one ergonomically:

    from ada.cad import CadConfig, TessellationPath, available_paths

    available_paths()                 # [OCC, ADACPP_LIBTESS2, ADACPP_OCC, ...] for this env
    cfg = CadConfig(path=TessellationPath.ADACPP_LIBTESS2, deflection=2.0)
    asm.cad_config = cfg              # attach to an Assembly
    stream_step_to_glb(step, glb, cad_config=cfg)   # or pass to a factory function

Availability is import-driven: ``occ`` needs pythonocc-core, the ``adacpp:*`` paths need the
``adacpp`` extension. ``CadConfig.default()`` prefers libtess2 when adacpp is installed (OCC-free,
step2glb-parity tessellation) and falls back to OCC otherwise.
"""

from __future__ import annotations

import importlib.util
import os
from dataclasses import dataclass
from enum import Enum
from functools import lru_cache

# Corpus-wide defaults for the NGEOM stream (libtess2/adacpp) tessellator. angular_deg caps
# the arc-segment span for any curved geometry (revolves, pipes, cylinders, B-splines) — the
# linear deflection alone can't keep a large-radius arc smooth because its sag tolerance grows
# with radius. 10 deg (was 20) keeps a 7 m-radius revolved beam's bulge apex within ~1% of true;
# it roughly matches the OCC path's 0.2 rad. Override per-run with ADA_STREAM_TESS_ANGULAR /
# ADA_STREAM_TESS_DEFLECTION or per-model via CadConfig.
DEFAULT_STREAM_TESS_DEFLECTION = 2.0
DEFAULT_STREAM_TESS_ANGULAR_DEG = 10.0

# Angular density mode. OFF: ``angular_deg`` is a fixed global ceiling for every curved surface
# (explicit-global-angle mode, backward compatible). ON: the ceiling is applied ADAPTIVELY per
# surface — a model-relative reference (``model_scale``, the model bbox diagonal) lets tiny curved
# features (bolts/pins in a large assembly, whose facets are sub-pixel) coarsen while large visible
# surfaces keep the fine angle. The relaxation itself lives in adacpp (angle_step); adapy only
# decides the mode and supplies ``model_scale``. Toggle with ADA_STREAM_TESS_ADAPTIVE.
#
# The default DIFFERS BY CALL PATH, deliberately — pass it explicitly via ``stream_tess_adaptive(default=)``:
#   * OFF for the per-object stream path (BatchTessellator): a library API whose mesh density must
#     not shift under callers who never asked for adaptive.
#   * ON for the whole-file native converters (STEP->GLB / ->OBJ / ->STL): dense curved assemblies
#     over-tessellate at a fixed fine angle, and these are the transfer-size- and timeout-sensitive
#     products (the crane's 107M-tri OBJ/STL blew the 5-min timeout).
DEFAULT_STREAM_TESS_ADAPTIVE = False
DEFAULT_STREAM_TESS_ADAPTIVE_NATIVE = True

# One falsy spelling for every ADA_STREAM_TESS_* boolean. Note "" is FALSY: an explicitly-empty env
# var means "off", not "on" (before this was centralised, scene_from_step_stream omitted "" from its
# set and so read an explicit ADA_STREAM_TESS_ADAPTIVE="" as ON while every other path read it OFF).
_TESS_ENV_FALSY = frozenset({"0", "false", "no", "off", ""})


def _tess_env_flag(name: str, default: bool) -> bool:
    """Read an ADA_STREAM_TESS_* boolean: unset => ``default``, else any non-falsy spelling => True."""
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() not in _TESS_ENV_FALSY


def stream_tess_defaults() -> tuple[float, float]:
    """(deflection, angular_deg) for the NGEOM stream path: env override else the corpus default.

    The single source of these two numbers — every stream/native tessellation entry point reads them
    through here so one nominal config can't mean different densities on different call paths.
    """
    defl = float(os.environ.get("ADA_STREAM_TESS_DEFLECTION", str(DEFAULT_STREAM_TESS_DEFLECTION)))
    ang = float(os.environ.get("ADA_STREAM_TESS_ANGULAR", str(DEFAULT_STREAM_TESS_ANGULAR_DEG)))
    return defl, ang


def stream_tess_adaptive(default: bool = DEFAULT_STREAM_TESS_ADAPTIVE) -> bool:
    """Whether adaptive per-surface angular density is enabled (env override else ``default``).

    ``default`` is the caller's mode when ADA_STREAM_TESS_ADAPTIVE is unset — it differs by call
    path on purpose (see DEFAULT_STREAM_TESS_ADAPTIVE / _NATIVE), so state it rather than relying on
    this signature's default.
    """
    return _tess_env_flag("ADA_STREAM_TESS_ADAPTIVE", default)


def stream_tess_face_regions() -> bool:
    """Whether to emit per-face clickable regions (face_ranges_node<m> in scenes[0].extras).

    Opt-in (ADA_STREAM_TESS_FACE_REGIONS=1): it bloats the GLB and forces serial face tessellation.
    """
    return _tess_env_flag("ADA_STREAM_TESS_FACE_REGIONS", False)


def stream_tess_strict() -> bool:
    """Whether a NGEOM->OCC tessellation fallback should raise instead of silently degrading
    (ADA_STREAM_TESS_STRICT=1) — enforces 100% stream-kernel coverage."""
    return _tess_env_flag("ADA_STREAM_TESS_STRICT", False)


def stream_tess_model_scale_env() -> float:
    """The raw ADA_STREAM_TESS_MODEL_SCALE (world units), 0.0 if unset/invalid — NO adaptive gating.

    For consumers *downstream* of the adaptive decision: the parent estimates the scale once per
    model and exports it, so its presence is itself the adaptive signal. A pool worker must not
    re-gate on ADA_STREAM_TESS_ADAPTIVE — it may not have inherited it, and re-gating would silently
    return 0.0 (fixed-angle) in every worker while the parent believed adaptive was on.
    Use ``stream_tess_model_scale`` when *you* are the one deciding.
    """
    try:
        return float(os.environ.get("ADA_STREAM_TESS_MODEL_SCALE", "0") or 0.0)
    except ValueError:
        return 0.0


def stream_tess_model_scale(default: bool = DEFAULT_STREAM_TESS_ADAPTIVE) -> float:
    """The model reference scale (world units) for adaptive density, or 0.0 (off).

    Adaptive is meaningful only with a model scale, so this returns 0 unless adaptive is enabled
    (env else ``default``) AND a scale is supplied via ADA_STREAM_TESS_MODEL_SCALE (the caller sets
    it once per model — the native STEP->GLB path estimates its own; see ada.cadit.step.model_scale).
    """
    if not stream_tess_adaptive(default=default):
        return 0.0
    return stream_tess_model_scale_env()


class CadBackendName(str, Enum):
    OCC = "occ"  # pythonocc-core (native BRepMesh)
    ADACPP = "adacpp"  # the adacpp extension (NGEOM / libtess2 + linked OCCT/CGAL/ifc kernels)


class TessellationPath(str, Enum):
    """A selectable (backend, tessellation-algorithm) pair.

    ``OCC`` is pythonocc's BRepMesh; the ``ADACPP_*`` paths map to adacpp ``tessellate_stream``
    pipelines (``libtess2`` is the OCC-free boundary CDT; ``occ``/``cgal``/``hybrid`` use adacpp's
    linked OCCT / ifcopenshell-taxonomy kernels)."""

    OCC = "occ"
    ADACPP_LIBTESS2 = "adacpp:libtess2"
    ADACPP_OCC = "adacpp:occ"
    ADACPP_CGAL = "adacpp:cgal"
    ADACPP_HYBRID = "adacpp:hybrid"

    @property
    def backend(self) -> CadBackendName:
        return CadBackendName.OCC if self is TessellationPath.OCC else CadBackendName.ADACPP

    @property
    def pipeline(self) -> str | None:
        """The adacpp ``tessellate_stream`` pipeline arg, or ``None`` for the OCC BRepMesh path."""
        return None if self is TessellationPath.OCC else self.value.split(":", 1)[1]


@lru_cache(maxsize=None)
def _module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def backend_available(backend: CadBackendName) -> bool:
    if backend is CadBackendName.ADACPP:
        return _module_available("adacpp")
    return _module_available("OCC")  # pythonocc-core package


def available_backends() -> list[CadBackendName]:
    """CAD backends importable in this environment."""
    return [b for b in CadBackendName if backend_available(b)]


def available_paths() -> list[TessellationPath]:
    """Tessellation paths usable in this environment (a path is listed when its backend imports;
    adacpp present => all of libtess2/occ/cgal/hybrid are listed)."""
    return [p for p in TessellationPath if backend_available(p.backend)]


class StepReader(str, Enum):
    """The STEP read path a factory (``ada.from_step`` / ``Part.read_step_file``) uses to turn a
    ``.stp`` file into adapy geometry.

    ``AUTO`` is the default: it parses with the kernel-free streaming reader (constant memory, no
    whole-model OCC materialisation) and falls back to the OCC OCAF reader only for files that use
    an entity outside the streaming reader's scope — so it is both the most memory-efficient path
    for the common case and as robust/complete as OCC for the rest (no geometry skipped). ``STREAM``
    is streaming-only (raises on out-of-scope entities). ``TOLERANT`` streams and *skips* the
    unsupported solids (never OOMs, but drops geometry — avoid as a default). ``OCC`` forces the
    whole-file OCC reader (needed for scale/transform/rotate-on-import). ``NATIVE`` forces
    adacpp's C++ NGEOM parser (fastest; ``AUTO`` probes it first when adacpp is available)."""

    AUTO = "auto"
    STREAM = "stream"
    TOLERANT = "tolerant"
    OCC = "occ"
    NATIVE = "native"


@dataclass
class CadConfig:
    """Selects the CAD read path + tessellation path + tolerances. Attach to ``Assembly.cad_config``
    or pass to a factory function (e.g. ``stream_step_to_glb(..., cad_config=cfg)`` or
    ``ada.from_step(..., cad_config=cfg)``)."""

    path: TessellationPath = TessellationPath.OCC
    deflection: float = DEFAULT_STREAM_TESS_DEFLECTION
    angular_deg: float = DEFAULT_STREAM_TESS_ANGULAR_DEG
    simplify: bool = False  # meshopt cleanup (step2glb merge parity); adacpp paths only
    # The STEP read path the factories default to. AUTO = constant-memory streaming with an OCC
    # fallback for out-of-scope files — the most memory-efficient + robust default. Override per
    # config to force a specific reader.
    step_reader: StepReader = StepReader.AUTO

    @classmethod
    def default(cls) -> "CadConfig":
        """Prefer libtess2 when adacpp is installed (OCC-free, step2glb parity), else OCC."""
        paths = available_paths()
        if TessellationPath.ADACPP_LIBTESS2 in paths:
            return cls(path=TessellationPath.ADACPP_LIBTESS2)
        return cls(path=TessellationPath.OCC)

    def validate(self) -> None:
        if self.path not in available_paths():
            raise ValueError(
                f"tessellation path {self.path.value!r} is not available in this environment; "
                f"available: {[p.value for p in available_paths()]}"
            )
        # Accept a bare string for ergonomics, but it must be a known reader.
        StepReader(self.step_reader)

    def env(self) -> dict[str, str]:
        """The streaming-export env vars this config maps to (read by the conversion worker)."""
        e = {"ADAPY_CAD_BACKEND": self.path.backend.value}
        if self.path.pipeline is not None:
            e["ADA_STREAM_TESS_PIPELINE"] = self.path.pipeline
            e["ADA_STREAM_TESS_DEFLECTION"] = repr(self.deflection)
            e["ADA_STREAM_TESS_ANGULAR"] = repr(self.angular_deg)
            if self.simplify:
                e["ADA_STREAM_SIMPLIFY"] = "1"
        return e

    def apply_env(self) -> None:
        """Set this config's env vars on ``os.environ`` (inherited by the conversion subprocess
        pool). Returns nothing; pair with a try/finally or use ``stream_step_to_glb(cad_config=)``."""
        os.environ.update(self.env())
