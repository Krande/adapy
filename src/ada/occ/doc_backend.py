"""OccDocBackend — pythonocc OCAF/XCAF document I/O for ada.cad.doc.DocBackend.

Relocated out of ada.cad.doc so the OCC dependency lives under ada.occ.
``ada.cad.doc.select_doc_backend`` lazy-imports this; ada.cad.doc re-exports it
via module ``__getattr__`` for back-compat.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ada.cad.doc import XCAF_DOC

if TYPE_CHECKING:
    from ada.occ.step.store import StepStore
    from ada.occ.step.writer import StepWriter


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
