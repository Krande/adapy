"""Backend-neutral handle for OCAF/XCAF assembly *document* I/O.

Kept separate from :mod:`ada.cad` ``CadBackend`` on purpose. This surface —
``STEPCAFControl`` reader/writer (names/colors/layers), the multi-shape
``RWGltf_CafWriter`` and the XCAF document model — is OCAF-heavy and is **not**
portable to adacpp's wasm build, which has no OCAF. So it lives behind an
optional, capability-gated ``DocBackend`` rather than the always-present shape
algebra of ``CadBackend``.

Selection mirrors ``ada.cad.select_backend``: explicit ``prefer`` →
``ADAPY_DOC_BACKEND`` env → auto-detect. Today only the OCC/XCAF backend
exists; an adacpp-wasm runtime would supply a *degraded* backend that omits
the ``"xcaf_doc"`` capability and steers callers to the portable per-shape
``write_glb_bytes`` + client-side scene assembly path instead. Use
:func:`require_capability` to gate the OCAF-only operations explicitly.

See dap plan/v3 notes_occ_backend_abstraction (Phase 6).
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from ada.occ.step.store import StepStore
    from ada.occ.step.writer import StepWriter

# Capability tokens (string-keyed so a backend can advertise a subset).
XCAF_DOC = "xcaf_doc"


@runtime_checkable
class DocBackend(Protocol):
    """Assembly document I/O contract. ``capabilities`` advertises which
    OCAF-dependent operations a concrete backend actually supports."""

    name: str
    capabilities: frozenset[str]

    def step_writer(self) -> "StepWriter": ...
    def step_reader(self, filepath: Any) -> "StepStore": ...
    def write_gltf(self, *args: Any, **kwargs: Any) -> Any: ...


class OccDocBackend:
    """OCAF/XCAF document I/O via pythonocc-core. Native CPython only — wraps
    the existing :class:`ada.occ.store.OCCStore` entry points (the OCC code is
    the implementation; this is the swap/capability boundary)."""

    name = "pythonocc-xcaf"
    capabilities = frozenset({XCAF_DOC})

    def __init__(self) -> None:
        # Probe OCAF availability so selection fails cleanly where pythonocc
        # (or its STEPCAF/RWGltf modules) isn't installable — mirrors
        # OccBackend's lazy-import-on-init pattern.
        from OCC.Core.RWGltf import RWGltf_CafWriter  # noqa: F401
        from OCC.Core.STEPCAFControl import STEPCAFControl_Writer  # noqa: F401

    def step_writer(self) -> "StepWriter":
        from ada.occ.store import OCCStore

        return OCCStore.get_step_writer()

    def step_reader(self, filepath: Any) -> "StepStore":
        from ada.occ.store import OCCStore

        return OCCStore.get_reader(filepath)

    def write_gltf(self, *args: Any, **kwargs: Any) -> Any:
        # The XCAF RWGltf_CafWriter path. The portable per-shape GLB path
        # (CadBackend.write_glb_bytes / the viewer's MeshStore concat) is the
        # adacpp-wasm degradation target and stays off this backend.
        from ada.occ.store import OCCStore

        return OCCStore.to_gltf(*args, **kwargs)


class AdacppDocBackend:
    """OCAF/XCAF document I/O via adacpp's bundled (native) OCCT. Lets STEP
    export run with no pythonocc installed — the native adacpp build links the
    full OCCT, so OCAF names/colors are available (unlike the wasm build).

    The RWGltf XCAF writer is not routed here yet; callers needing it should use
    the portable per-shape ``CadBackend.write_glb_bytes`` path or the OCC doc
    backend."""

    name = "adacpp-xcaf"
    capabilities = frozenset({XCAF_DOC})

    def __init__(self) -> None:
        # Probe that adacpp + its STEP read/write are importable so selection
        # fails cleanly where the native adacpp build isn't present.
        from adacpp import cad as _cad  # noqa: F401

        if not hasattr(_cad, "write_step") or not hasattr(_cad, "read_step_shapes"):
            raise ImportError("adacpp build lacks cad.write_step / read_step_shapes (upgrade ada-cpp)")

    def step_writer(self) -> "StepWriter":
        from ada.cadit.step.write.adacpp_writer import AdacppStepWriter

        return AdacppStepWriter("AdaStep")

    def step_reader(self, filepath: Any) -> "StepStore":
        from ada.cadit.step.read.adacpp_store import AdacppStepStore

        return AdacppStepStore(filepath)

    def write_gltf(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError(
            "AdacppDocBackend.write_gltf: use the portable per-shape "
            "CadBackend.write_glb_bytes path under the adacpp backend."
        )


def select_doc_backend(prefer: str | None = None) -> DocBackend:
    """Pick a document backend. ``prefer`` overrides everything, then
    ``ADAPY_DOC_BACKEND``; otherwise align with the CAD backend choice
    (``ADAPY_CAD_BACKEND``) and fall back to whichever kernel is importable."""
    choice = prefer or os.environ.get("ADAPY_DOC_BACKEND")
    if choice in ("adacpp", "adacpp-xcaf"):
        return AdacppDocBackend()
    if choice in ("occ", "pythonocc-core", "pythonocc-xcaf", "xcaf"):
        return OccDocBackend()
    if choice is not None:
        raise ValueError(f"Unknown ADAPY_DOC_BACKEND: {choice!r}")

    # No explicit doc backend: honour the active CAD backend choice first.
    if os.environ.get("ADAPY_CAD_BACKEND") == "adacpp":
        try:
            return AdacppDocBackend()
        except ImportError:
            pass

    try:
        return OccDocBackend()
    except ImportError:
        # Pure-adacpp environment (no pythonocc): fall back to the adacpp doc backend.
        try:
            return AdacppDocBackend()
        except ImportError as e:
            raise ImportError(
                "No document backend available — install `pythonocc-core` or `ada-cpp` "
                f"for OCAF/XCAF assembly I/O. Last error: {e}"
            )


_ACTIVE_DOC_BACKEND: DocBackend | None = None


def active_doc_backend() -> DocBackend:
    """Return the process-wide document backend, selecting and memoizing one
    on first use. Override with ``ADAPY_DOC_BACKEND`` or
    :func:`reset_active_doc_backend`."""
    global _ACTIVE_DOC_BACKEND
    if _ACTIVE_DOC_BACKEND is None:
        _ACTIVE_DOC_BACKEND = select_doc_backend()
    return _ACTIVE_DOC_BACKEND


def reset_active_doc_backend() -> None:
    """Drop the memoized document backend (tests / runtime env switch)."""
    global _ACTIVE_DOC_BACKEND
    _ACTIVE_DOC_BACKEND = None


def require_capability(backend: DocBackend, capability: str) -> None:
    """Raise if ``backend`` does not advertise ``capability``. The explicit
    gate for OCAF-only operations — under a degraded (adacpp-wasm) backend the
    caller should fall back to the portable per-shape path instead."""
    if capability not in backend.capabilities:
        raise NotImplementedError(
            f"Document backend {backend.name!r} lacks the {capability!r} capability "
            "(OCAF/XCAF is unavailable here — use the portable per-shape path)."
        )


__all__ = [
    "XCAF_DOC",
    "DocBackend",
    "OccDocBackend",
    "AdacppDocBackend",
    "active_doc_backend",
    "require_capability",
    "reset_active_doc_backend",
    "select_doc_backend",
]
