"""FEA viewer artefact bake — mesh GLB + per-field step-stack blobs + manifest.

Phase 1 of the streaming FEA viewer pipeline. The bake runs once per
source and produces three artefact kinds:

* ``fea.mesh.glb`` — geometry-only GLB (no animation, no vertex colour).
* ``fea.<field>.bin`` — one binary blob per field, all steps, step-major.
  Header is JSON in a fixed 1 KB prefix; payload is a contiguous
  ``[n_steps × n_points × n_components]`` float32 array. Frontend
  range-fetches by step.
* ``fea.manifest.json`` — catalogue: mesh metadata, per-field metadata,
  pre-computed scalar ranges so the colormap stays fixed across steps.

The bake consumes a streaming reader (`FEAStreamReader` protocol) so
fields are written one step at a time — no need to hold the full
``[n_steps × n_points × n_components]`` in memory before write. RMED
has a native streaming reader (``med_stream_reader``); other formats
adapt their existing eager readers via :class:`FEAResultStreamAdapter`.
"""

from __future__ import annotations

from .bake import BakeResult, bake_artefacts, bake_fea_artefacts_from_source
from .beam_solids import (  # noqa: F401
    _dedup_beam_tessellation as _dedup_beam_tessellation,
)
from .beam_solids import (
    tessellate_beams_to_solid_mesh,
    write_beam_solids_edges,
    write_beam_solids_elements,
    write_beam_solids_glb,
    write_beam_solids_warp,
)
from .fields import _encode_blob_header as _encode_blob_header  # noqa: F401
from .fields import (  # noqa: F401
    _encode_elem_field_blob_header as _encode_elem_field_blob_header,
)
from .fields import (
    read_blob_header,
    read_blob_step,
    read_elem_field_blob_header,
    read_elem_field_blob_step,
    write_element_field_blob_streaming,
    write_field_blob_streaming,
)
from .formats import (
    BEAM_WARP_ENTRY_BYTES,
    BEAM_WARP_HEADER_BYTES,
    BEAM_WARP_MAGIC,
    BEAM_WARP_VERSION,
    BLOB_HEADER_BYTES,
    BLOB_MAGIC,
    BLOB_VERSION,
    EDGE_HEADER_BYTES,
    EDGE_MAGIC,
    EDGE_VERSION,
    ELEM_ENTRY_BYTES,
    ELEM_FIELD_HEADER_BYTES,
    ELEM_FIELD_MAGIC,
    ELEM_FIELD_VERSION,
    ELEM_HEADER_BYTES,
    ELEM_MAGIC,
    ELEM_VERSION,
    FEA_BAKE_VERSION,
    MANIFEST_VERSION,
)
from .history import (
    HistoryDomain,
    HistoryRecords,
    HistoryRegion,
    HistoryRegionKind,
    HistorySeries,
    HistoryStep,
    HistoryVariable,
    build_history_payload,
)
from .manifest import _default_view_for as _default_view_for  # noqa: F401
from .manifest import _format_step_label as _format_step_label  # noqa: F401
from .manifest import (  # noqa: F401
    _format_step_label_simple as _format_step_label_simple,
)
from .manifest import _infer_analysis_kind as _infer_analysis_kind  # noqa: F401
from .manifest import _presentation_payload as _presentation_payload  # noqa: F401
from .manifest import _step_entry as _step_entry  # noqa: F401
from .manifest import _value_label_key as _value_label_key  # noqa: F401
from .manifest import analysis_kind_from_result_cases, build_manifest, write_manifest
from .mesh import _compute_topology as _compute_topology  # noqa: F401
from .mesh import (
    write_mesh_edges,
    write_mesh_elements,
    write_mesh_glb,
    write_mesh_line_edges,
)
from .posters import BakeWithPostersResult
from .posters import _resolve_mode_selection as _resolve_mode_selection  # noqa: F401
from .posters import bake_with_posters, bake_with_posters_from_source
from .protocol import FEAStreamReader
from .readers import _STREAM_READERS as _STREAM_READERS  # noqa: F401
from .readers import (  # noqa: F401
    _ensure_builtin_stream_readers as _ensure_builtin_stream_readers,
)
from .readers import _make_fem_reader as _make_fem_reader  # noqa: F401
from .readers import _make_rmed_reader as _make_rmed_reader  # noqa: F401
from .readers import _make_sif_reader as _make_sif_reader  # noqa: F401
from .readers import _make_sin_reader as _make_sin_reader  # noqa: F401
from .readers import _StreamReaderFactory as _StreamReaderFactory  # noqa: F401
from .readers import (
    fea_artefact_extensions,
    is_fea_artefact_source,
    make_stream_reader,
    register_stream_reader,
)
from .specs import (
    ElementFieldArtefactMeta,
    ElementFieldSpec,
    ElementStepValues,
    FieldArtefactMeta,
    FieldCategory,
    FieldSpec,
    MeshGeometry,
    SolidBeamMesh,
    StepValues,
)
from .stream_adapter import FEAResultStreamAdapter
from .stream_adapter import _classify_field as _classify_field  # noqa: F401
from .stream_adapter import (  # noqa: F401
    _ip_layout_from_int_positions as _ip_layout_from_int_positions,
)

__all__ = [
    "BLOB_MAGIC",
    "BLOB_VERSION",
    "BLOB_HEADER_BYTES",
    "MANIFEST_VERSION",
    "FEA_BAKE_VERSION",
    "ELEM_FIELD_MAGIC",
    "ELEM_FIELD_VERSION",
    "ELEM_FIELD_HEADER_BYTES",
    "EDGE_MAGIC",
    "EDGE_VERSION",
    "EDGE_HEADER_BYTES",
    "ELEM_MAGIC",
    "ELEM_VERSION",
    "ELEM_HEADER_BYTES",
    "ELEM_ENTRY_BYTES",
    "BEAM_WARP_MAGIC",
    "BEAM_WARP_VERSION",
    "BEAM_WARP_HEADER_BYTES",
    "BEAM_WARP_ENTRY_BYTES",
    "MeshGeometry",
    "SolidBeamMesh",
    "FieldCategory",
    "FieldSpec",
    "StepValues",
    "ElementFieldSpec",
    "ElementStepValues",
    "FieldArtefactMeta",
    "ElementFieldArtefactMeta",
    "FEAStreamReader",
    "HistoryRegionKind",
    "HistoryDomain",
    "HistoryRegion",
    "HistoryVariable",
    "HistoryStep",
    "HistorySeries",
    "HistoryRecords",
    "build_history_payload",
    "FEAResultStreamAdapter",
    "tessellate_beams_to_solid_mesh",
    "write_beam_solids_glb",
    "write_beam_solids_warp",
    "write_beam_solids_elements",
    "write_beam_solids_edges",
    "write_mesh_glb",
    "write_mesh_edges",
    "write_mesh_line_edges",
    "write_mesh_elements",
    "write_field_blob_streaming",
    "write_element_field_blob_streaming",
    "read_blob_header",
    "read_blob_step",
    "read_elem_field_blob_header",
    "read_elem_field_blob_step",
    "analysis_kind_from_result_cases",
    "build_manifest",
    "write_manifest",
    "register_stream_reader",
    "fea_artefact_extensions",
    "is_fea_artefact_source",
    "make_stream_reader",
    "bake_fea_artefacts_from_source",
    "BakeResult",
    "bake_artefacts",
    "BakeWithPostersResult",
    "bake_with_posters",
    "bake_with_posters_from_source",
]
