"""Composable conversion plans: a serializer + a tessellator, resolved at execute time.

WHY THIS EXISTS. A →GLB conversion has two INDEPENDENT axes — which code path reads the
source (the *serializer*) and which kernel meshes it (the *tessellator*) — and until now
adapy had no object for either. The pair was encoded as two loosely-related string knobs
(``step_glb_pipeline`` naming a code path, ``glb_tess_engine`` naming a kernel) that the
REST layer packed tokens into and the library unpacked. That works, but it means the axes
only exist in the API's vocabulary: a library caller cannot say "native reader, CDT
kernel" without knowing which knob to park which token on.

WHAT IT ADDS. ``Serializer | Tessellator`` builds a :class:`ConversionPlan`. Building is
INERT — no import of adacpp, no capability probe, no work. ``execute()`` is the only thing
that resolves, and it is where the plan can FUSE: when the pair is the native reader plus a
kernel that reader can drive, it collapses to adacpp's single in-process C++ call instead
of the Python reader + tessellation pool. Same pair, two implementations, chosen at the
point where the answer is knowable.

WHAT IT DOES NOT DO. It does not own the track vocabulary — track names come from
``adacpp.cad.tess_tracks()`` via the registry, so adding a track stays a change in adacpp
alone. It does not replace :class:`~ada.cad.registry.CadConfig`: a plan LOWERS to one
(``to_cad_config``), which is what carries selection into the worker's subprocesses via
``CadConfig.env()``. The plan is the front door; CadConfig remains the transport.

VALIDATION IS INTRINSIC. Every capability question is asked, never assumed, and an
unanswerable plan raises instead of quietly becoming a different plan. This is not
defensiveness for its own sake: a serializer that accepts a kernel it cannot run and
silently meshes with another one is indistinguishable from success at every layer above it
— which is exactly how a native path shipped every conversion as libtess2 while reporting
the caller's choice back to them.
"""

from __future__ import annotations

import pathlib
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from ada.cad.registry import (
    DEFAULT_STREAM_TESS_ANGULAR_DEG,
    DEFAULT_STREAM_TESS_DEFLECTION,
    CadBackendName,
    CadConfig,
    StepReader,
    TessTrack,
    available_tess_tracks,
    tess_track_by_name,
)

if TYPE_CHECKING:
    from collections.abc import Callable


class PlanError(ValueError):
    """A plan that cannot be honoured as written.

    Always raised in preference to running a DIFFERENT plan than the one asked for.
    """


# Serializer tokens. adapy's own vocabulary (unlike the tracks): these name code paths in this
# package, not anything adacpp declares. The REST layer's `serializer` axis uses the same two names
# for the server-side paths; its browser-side 'wasm' serializer never reaches a worker and so has no
# executable plan here.
SERIALIZER_CPP = "cpp"
SERIALIZER_PYTHON = "python"


@dataclass(frozen=True)
class Tessellator:
    """A tessellation track plus the knobs that shape its output.

    ``track`` is a DISCOVERED name (see ``available_tess_tracks``) — "adacpp:cdt", "occ", … — not
    an enum, so a track added in a later adacpp is nameable here without a change to adapy.
    """

    track: str
    deflection: float = DEFAULT_STREAM_TESS_DEFLECTION
    angular_deg: float = DEFAULT_STREAM_TESS_ANGULAR_DEG
    # None => let the executing path apply its own default (they differ deliberately: OFF for the
    # per-object library API, ON for whole-file converters — see registry's DEFAULT_STREAM_TESS_
    # ADAPTIVE vs _NATIVE). Set explicitly to pin it.
    adaptive: bool | None = None
    face_regions: bool = False

    @classmethod
    def default(cls, **kwargs) -> Tessellator:
        """The track adacpp declares as its default (libtess2 today), or adapy's OCC track."""
        tracks = available_tess_tracks()
        chosen = next((t for t in tracks if t.is_default), None) or (tracks[0] if tracks else None)
        if chosen is None:
            raise PlanError("no tessellation track is available in this environment")
        return cls(track=chosen.name, **kwargs)

    @property
    def resolved(self) -> TessTrack | None:
        """The registry's TessTrack for this name, or None if this environment has no such track."""
        return tess_track_by_name(self.track)

    def __ror__(self, other: Serializer) -> ConversionPlan:
        # Supports `Serializer | Tessellator` regardless of which side defines __or__.
        if isinstance(other, Serializer):
            return ConversionPlan(serializer=other, tessellator=self)
        return NotImplemented


@dataclass(frozen=True)
class Serializer:
    """A code path that reads the source and writes the target, plus its own knobs."""

    name: str
    # 0 = auto (hardware concurrency, cgroup-clamped). Honoured by the native path; the Python
    # path sizes its own pool.
    threads: int = 0
    # Bake EXT_meshopt_compression in the writer. Native only — see ConversionPlan.validate.
    meshopt: bool = True
    step_reader: StepReader = StepReader.AUTO

    @classmethod
    def cpp(cls, **kwargs) -> Serializer:
        """The fully-native path: C++ reader + tessellation + GLB writer, in-process."""
        return cls(name=SERIALIZER_CPP, **kwargs)

    @classmethod
    def python(cls, **kwargs) -> Serializer:
        """The Python-orchestrated path: streaming reader -> NGEOM -> kernel, via a worker pool."""
        return cls(name=SERIALIZER_PYTHON, **kwargs)

    def __or__(self, other: Tessellator) -> ConversionPlan:
        if isinstance(other, Tessellator):
            return ConversionPlan(serializer=self, tessellator=other)
        return NotImplemented


@dataclass(frozen=True)
class ConversionPlan:
    """A serializer + tessellator pair. Inert until :meth:`execute`."""

    serializer: Serializer
    tessellator: Tessellator

    # --- resolution ---------------------------------------------------------------------

    def with_(self, **kwargs) -> ConversionPlan:
        """A copy with tessellator knobs replaced — for stacking a base plan into variants."""
        return replace(self, tessellator=replace(self.tessellator, **kwargs))

    def fuses(self, source: str | pathlib.Path, target: str = "glb") -> bool:
        """True if :meth:`execute` will collapse this pair into adacpp's single native call.

        Only STEP->GLB fuses today: that is the one whole-file C++ entry point that exists.
        Answers what WILL happen, so a caller can log or assert it rather than infer it from
        timings.
        """
        if self.serializer.name != SERIALIZER_CPP or target != "glb":
            return False
        if pathlib.Path(source).suffix.lower() not in (".step", ".stp"):
            return False
        from ada.cadit.step.native_step_to_glb import native_adacpp_available

        return native_adacpp_available()

    def validate(self, source: str | pathlib.Path | None = None, target: str = "glb") -> None:
        """Raise :class:`PlanError` if this plan cannot be run exactly as written.

        Checks the SAME facts the REST layer's dropdown filters on, so what is offered and what
        runs cannot drift apart.
        """
        track = self.tessellator.resolved
        if track is None:
            names = [t.name for t in available_tess_tracks()]
            raise PlanError(f"unknown tessellation track {self.tessellator.track!r}; available: {names}")

        if self.serializer.name not in (SERIALIZER_CPP, SERIALIZER_PYTHON):
            raise PlanError(
                f"unknown serializer {self.serializer.name!r}; expected {SERIALIZER_CPP!r} or {SERIALIZER_PYTHON!r}"
            )

        if self.serializer.name != SERIALIZER_CPP:
            return

        from ada.cadit.step.native_step_to_glb import (
            native_adacpp_available,
            native_track_selection_available,
        )

        if not native_adacpp_available():
            raise PlanError("serializer 'cpp' needs adacpp's native STEP->GLB entry point, which is not importable")
        if track.backend is not CadBackendName.ADACPP:
            raise PlanError(
                f"serializer 'cpp' cannot run track {track.name!r} ({track.backend.value} backend); "
                f"the native path only drives adacpp tracks"
            )
        # Declared by adacpp: the taxonomy kernels need ifcopenshell geometry the C++ reader never
        # builds, and adacpp meshes them as though untracked instead of erroring.
        if not track.neutral:
            raise PlanError(
                f"serializer 'cpp' cannot run track {track.name!r}: it is a taxonomy kernel and the "
                f"native reader builds no taxonomy geometry (it would mesh as if untracked). Use "
                f"serializer 'python' for it, or a neutral track."
            )
        if not native_track_selection_available() and not track.is_default:
            raise PlanError(
                f"this adacpp build's native binding takes no 'pipeline' (< 0.16), so track "
                f"{track.name!r} cannot be honoured — it would silently run the default track"
            )
        if self.tessellator.face_regions and source is not None and not self.fuses(source, target):
            raise PlanError("face_regions is only produced by the native STEP->GLB path")

    # --- lowering -----------------------------------------------------------------------

    def to_cad_config(self) -> CadConfig:
        """Lower to the flat config the existing factories and the worker's env transport take.

        CadConfig is what ``env()`` serializes into a subprocess, so this is the plan's contract
        with everything that predates it.
        """
        return CadConfig(
            path=self.tessellator.track,
            deflection=self.tessellator.deflection,
            angular_deg=self.tessellator.angular_deg,
            step_reader=self.serializer.step_reader,
        )

    # --- execution ----------------------------------------------------------------------

    def execute(
        self,
        source: str | pathlib.Path,
        target_path: str | pathlib.Path,
        *,
        on_progress: Callable[[str, float], None] | None = None,
    ) -> dict:
        """Run the plan, fusing to the native path when this pair permits it.

        Returns the executing path's stats dict. Raises PlanError before doing any work if the
        plan cannot be honoured as written — never substitutes a different serializer or kernel.
        """
        source = pathlib.Path(source)
        self.validate(source=source, target="glb")

        if self.fuses(source, "glb"):
            return self._execute_native(source, target_path, on_progress=on_progress)
        return self._execute_python(source, target_path, on_progress=on_progress)

    def _execute_native(self, source, target_path, *, on_progress) -> dict:
        from ada.cadit.step.native_step_to_glb import native_step_to_glb

        track = self.tessellator.resolved
        with _tess_env(self.tessellator):
            return native_step_to_glb(
                source,
                target_path,
                deflection=self.tessellator.deflection,
                angular_deg=self.tessellator.angular_deg,
                num_threads=self.serializer.threads,
                meshopt=self.serializer.meshopt,
                on_progress=on_progress,
                # adacpp's own token ("cdt"), not the registry's namespaced name ("adacpp:cdt").
                pipeline=track.pipeline if track else None,
            )

    def _execute_python(self, source, target_path, *, on_progress) -> dict:
        from ada.cadit.step.stream_to_glb import stream_step_to_glb

        with _tess_env(self.tessellator):
            return stream_step_to_glb(
                source,
                target_path,
                on_progress=on_progress,
                cad_config=self.to_cad_config(),
            )


class _tess_env:
    """Apply the tessellator knobs that ONLY travel by environment, and restore them after.

    ``adaptive`` and ``face_regions`` are read from env deep inside both paths (and inherited by
    the Python path's subprocess pool) rather than passed down as arguments, so a plan that pinned
    them has no other way to say so. Scoped rather than set-and-forget: a plan must not leak its
    choices into the next conversion in the same process.
    """

    def __init__(self, tess: Tessellator):
        self._tess = tess
        self._saved: dict[str, str | None] = {}

    def __enter__(self):
        import os

        env: dict[str, str] = {}
        if self._tess.adaptive is not None:
            env["ADA_STREAM_TESS_ADAPTIVE"] = "1" if self._tess.adaptive else "0"
        if self._tess.face_regions:
            env["ADA_STREAM_TESS_FACE_REGIONS"] = "1"
        self._saved = {k: os.environ.get(k) for k in env}
        os.environ.update(env)
        return self

    def __exit__(self, *exc):
        import os

        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        return False
