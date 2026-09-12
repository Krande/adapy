"""Mesh and field data specs, the stream-reader protocol, and per-artefact metadata records."""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field as dc_field
from typing import Literal

import numpy as np

from ada.fem.results.common import CellBlockData
from ada.fem.results.field_data import FieldPresentation


@dataclass
class MeshGeometry:
    """Geometry-only mesh. The streaming reader produces one of these
    once per source; downstream the writer turns it into the mesh GLB."""

    points: np.ndarray  # (n_points, 3) float
    cell_blocks: list[CellBlockData]
    # Solver node ids aligned to ``points`` rows, when the reader knows them.
    # What a node-number label or readout prints — a row index is not a node
    # number on a deck with renumbered or sparse ids. Optional: readers
    # without identifiers leave it None and the manifest omits ``node_labels``.
    node_labels: list[int] | None = None


@dataclass
class SolidBeamMesh:
    """Beam elements tessellated as 3D extruded solids. Optional bake
    output — only emitted when the reader has section + axis info per
    beam element. The data shape mirrors the main mesh plus a per-
    vertex warp mapping so the solid mesh can deform in lockstep with
    its parent beam's nodal displacements:

    * ``points``: (n_verts, 3) float64 — merged vertex buffer across
      all beams.
    * ``triangles``: (n_tris, 3) uint32 — indices into ``points``.
    * ``element_ranges``: one :class:`ElementRange` per beam, keyed by
      the line-element label so the frontend can paint AFEL element
      fields onto the solid faces with the same draw-range lookup as
      the main mesh.
    * ``vertex_node0`` / ``vertex_node1``: (n_verts,) uint32 — the
      0-based indices of the parent beam's two endpoint nodes in the
      main mesh's point buffer. Same value across all vertices owned
      by one beam.
    * ``vertex_t``: (n_verts,) float32 — axial parameter ∈ [0, 1] of
      each vertex along its parent beam: the projection of
      (v - p_n0) onto the (p_n1 - p_n0) direction. The frontend warp
      path computes per-vertex displacement as
      ``lerp(disp[node0], disp[node1], t)`` so a scaled deformation
      keeps the solid beam connected at both ends.
    """

    points: np.ndarray
    triangles: np.ndarray
    # Forward reference — ``ElementRange`` lives in
    # ``ada.visit.rendering.femviz`` to avoid circular imports between
    # the bake and the topology helper. Typed as ``list`` to keep this
    # module import-light; ``write_beam_solids_elements`` does the
    # structural validation at write time.
    element_ranges: list = dc_field(default_factory=list)
    vertex_node0: np.ndarray = dc_field(default_factory=lambda: np.empty(0, dtype=np.uint32))
    vertex_node1: np.ndarray = dc_field(default_factory=lambda: np.empty(0, dtype=np.uint32))
    vertex_t: np.ndarray = dc_field(default_factory=lambda: np.empty(0, dtype=np.float32))
    # Coverage telemetry — populated by ``try_solid_beams`` so the
    # caller (and tests) can see how complete the solid-beam render
    # is without parsing worker logs. ``total_beams`` is the count
    # of line elements the reader saw; ``skip_reasons`` buckets the
    # failures by category ("no-section", "genbeam-no-profile",
    # "occ-error[StdFail_NotDone]", ...).
    total_beams: int = 0
    skip_reasons: dict = dc_field(default_factory=dict)


# Field category — coarse semantic label used by the viewer to decide
# whether a field should drive mesh deformation (only ``displacement``
# does), whether the deformation toggle should default ON (everything
# except ``reaction``), and how to label the field in pickers. The
# readers tag each FieldSpec explicitly; ``other`` is the fallback
# when the reader can't classify (a third-party field, an unknown RV
# card). Adding a new category should be deliberate — the frontend
# switch on this is exhaustive.
FieldCategory = Literal["displacement", "reaction", "stress", "strain", "property", "other"]


@dataclass
class FieldSpec:
    """Per-field metadata needed to plan a blob write before the first
    step is read. ``step_values`` is the time / eigenfrequency value
    per step; ``components`` are component names (e.g. ``["DX","DY","DZ"]``).
    """

    name: str
    components: list[str]
    n_steps: int
    n_points: int
    support: Literal["nodal", "element_nodal", "element_average", "result_point", "line_result_point", "gauss"]
    step_values: list[float]
    category: FieldCategory = "other"
    dtype: np.dtype = np.dtype(np.float32)
    presentation: FieldPresentation | None = None
    analysis_kind: Literal["static", "eigen"] | None = None

    @property
    def n_components(self) -> int:
        return len(self.components)

    @property
    def kind(self) -> str:
        n = self.n_components
        if n == 1:
            return "scalar"
        if n == 3:
            return "vector3"
        if n == 6:
            return "vector6"
        if n == 9:
            return "tensor9"
        return f"vector{n}"


@dataclass
class StepValues:
    """One step of one field, as the streaming reader yields it."""

    step_index: int
    step_value: float
    values: np.ndarray  # (n_points, n_components)


@dataclass
class ElementFieldSpec:
    """Per-element-type element-field metadata. Element fields differ
    from nodal in two ways: values live at integration points inside
    the element (not at nodes), and the IP layout depends on the
    element type. We emit one blob per ``(field_name, elem_type)``
    so the frontend can fetch only the buckets it draws and the
    payload shape is uniform within each blob.

    ``element_labels`` is the topology-walk-order list of element
    labels for this type; downstream artefacts (AFEM, picking)
    reference labels by value, so this alignment is load-bearing.

    ``ip_layout`` carries enough metadata for the frontend's layer +
    IP pickers (e.g., ``{"layer": "top", "in_plane": "corner_2"}``).
    Optional — readers that don't know the layout can leave it empty
    and the frontend falls back to numeric IP indices."""

    name: str
    components: list[str]
    n_steps: int
    elem_type: str
    n_elements: int
    n_ips: int
    element_labels: list[int]
    step_values: list[float]
    element_node_indices: list[list[int]] = dc_field(default_factory=list)
    ip_layout: list[dict] = dc_field(default_factory=list)
    category: FieldCategory = "other"
    support: Literal["element_nodal", "element_average", "result_point", "line_result_point", "gauss"] = "gauss"
    dtype: np.dtype = np.dtype(np.float32)
    presentation: FieldPresentation | None = None
    analysis_kind: Literal["static", "eigen"] | None = None

    @property
    def n_components(self) -> int:
        return len(self.components)


@dataclass
class ElementStepValues:
    """One step of one element-field, as the streaming reader yields it."""

    step_index: int
    step_value: float
    # (n_elements, n_ips, n_components), ordered by ``ElementFieldSpec.element_labels``.
    values: np.ndarray


@dataclass
class FieldArtefactMeta:
    """Bake output per field — used to compose the manifest entry."""

    spec: FieldSpec
    blob_filename: str
    stride_bytes: int
    scalar_range_per_component: dict[str, tuple[float, float]]
    scalar_range_magnitude: tuple[float, float]


# ---------------------------------------------------------------------------
# Element-field blob writer (streaming)
# ---------------------------------------------------------------------------


@dataclass
class ElementFieldArtefactMeta:
    """Bake output per (field, elem_type) — composes one per_type
    entry in the field's manifest record."""

    spec: ElementFieldSpec
    blob_filename: str
    stride_bytes: int
    scalar_range_per_component: dict[str, tuple[float, float]]
    scalar_range_magnitude: tuple[float, float]
