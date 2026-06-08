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

import json
import os
import pathlib
import struct
from dataclasses import dataclass
from dataclasses import field as dc_field
from typing import Callable, Iterable, Iterator, Literal, Protocol

import numpy as np

from ada.fem.results.common import CellBlockData

# Binary format: 4-byte magic, uint32 version, uint32 json_len, JSON
# header, zero-padded to 1024 bytes, then payload. Header version
# bump signals a breaking layout change (e.g. variable-stride steps).
BLOB_MAGIC = b"AFBL"
BLOB_VERSION = 1
BLOB_HEADER_BYTES = 1024
# v2 adds the optional ``history`` section (time-series at monitored
# nodes / elements). v1 manifests carry only mesh + fields; v2 readers
# treat ``history`` as optional so old artefacts keep loading.
MANIFEST_VERSION = 2

# Element-field blob format (AFEL). Same fixed-header pattern as
# AFBL, distinct magic so the frontend can fail fast if it loads the
# wrong sidecar. Payload shape per blob is
# ``[n_steps, n_elements, n_ips, n_components]`` float32. One blob
# per ``(field_name, elem_type)`` — different element types in the
# same field don't share IP counts, so one blob per type keeps the
# layout uniform.
ELEM_FIELD_MAGIC = b"AFEL"
ELEM_FIELD_VERSION = 1
# Same 1 KB prefix as AFBL — header carries only O(1) binary shape
# metadata. ``element_labels`` and ``ip_layout`` live in the
# manifest's ``per_type`` entry where they can grow with the model
# size without bloating the binary header.
ELEM_FIELD_HEADER_BYTES = 1024

# Mesh-edge sidecar format. Distinct from AFBL: edges are static
# per source (one-shot, no step stack) and small (~10s of KB), so
# the header is just magic + version + count, no JSON metadata.
# Frontend renders these as THREE.LineSegments sharing the mesh's
# position attribute, so deformation drives both face and line
# rendering from the same buffer.
EDGE_MAGIC = b"AFEG"
EDGE_VERSION = 1
EDGE_HEADER_BYTES = 16  # magic + version + n_edges + 4-byte pad

# Mesh-element sidecar format (AFEM). One entry per element: the
# source-file label and the element's range into the flat triangle
# buffer of fea.mesh.glb. Frontend hydrates these into
# ``userdata.id_hierarchy`` + ``userdata.draw_ranges_<meshName>`` so
# CustomBatchedMesh's existing pick → highlight pipeline picks up
# the FEA mesh without a parallel selection path.
ELEM_MAGIC = b"AFEM"
ELEM_VERSION = 1
ELEM_HEADER_BYTES = 16  # magic + version + n_elements + 4-byte pad
ELEM_ENTRY_BYTES = 12  # uint32 label, uint32 tri_start, uint32 tri_count

# Beam-solid mesh — optional parallel mesh emitted by readers that
# have section + axis info per beam element (currently SIF only via
# the FEAResultStreamAdapter). The bake tessellates each beam's
# extruded cross-section into triangles, concatenates them into one
# vertex + index pair, and records per-beam draw ranges keyed by the
# line-element label. Frontend renders this as a second mesh
# alongside ``fea.mesh.glb`` and can paint it with the same AFEL
# element-field pipeline since the labels match.

# Beam-solid warp sidecar (AFBV). Per-vertex mapping back to the
# nodal displacement field: (node0_idx, node1_idx, t). The frontend
# lerps disp[node0]<-->disp[node1] by ``t`` per vertex so the solid
# mesh deforms in lockstep with its parent beam's two endpoints —
# without this, a large scaleFactor on a static load would make the
# shells flex while the rigid beam solids stayed put, visually
# disconnecting the structure.
BEAM_WARP_MAGIC = b"AFBV"
BEAM_WARP_VERSION = 1
BEAM_WARP_HEADER_BYTES = 16  # magic + version + n_verts + 4-byte pad
BEAM_WARP_ENTRY_BYTES = 12  # uint32 n0, uint32 n1, float32 t


@dataclass
class MeshGeometry:
    """Geometry-only mesh. The streaming reader produces one of these
    once per source; downstream the writer turns it into the mesh GLB."""

    points: np.ndarray  # (n_points, 3) float
    cell_blocks: list[CellBlockData]


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
FieldCategory = Literal["displacement", "reaction", "stress", "strain", "other"]


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
    support: Literal["nodal", "element_nodal", "gauss"]
    step_values: list[float]
    category: FieldCategory = "other"
    dtype: np.dtype = np.dtype(np.float32)

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
    ip_layout: list[dict] = dc_field(default_factory=list)
    category: FieldCategory = "other"
    support: Literal["element_nodal", "gauss"] = "gauss"
    dtype: np.dtype = np.dtype(np.float32)

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


# ---------------------------------------------------------------------------
# History output (time-series at monitored nodes / elements / model)
# ---------------------------------------------------------------------------
#
# Field outputs are dense 3D paintings; history outputs are a sparse
# time series at a hand-picked set of points the analyst requested.
# Their natural axes differ (region × variable × step × time), so the
# manifest carries them in a separate ``history`` section rather than
# trying to share the field machinery.
#
# v1 of the section embeds the (times, values) arrays directly in the
# manifest JSON. That fits typical sizes (a few thousand frames × a
# few dozen variables × a few regions ≈ low-MB JSON); if a future
# analysis pushes past that, the shape leaves room to swap inline
# arrays for a binary blob without changing the surrounding keys.

HistoryRegionKind = Literal["node", "element", "model", "set"]
HistoryDomain = Literal["time", "frequency", "mode"]


@dataclass
class HistoryRegion:
    """A monitored point or set the source's history output reports on.

    ``id`` is the bake-local key the series rows refer back to; the
    other fields drive the picker UI. ``instance`` is the part /
    instance name ("ASSEMBLY" for model-scoped output). ``coords`` is
    optional metadata — present for node regions where the reader can
    look the position up, omitted otherwise."""

    id: str
    kind: HistoryRegionKind
    instance: str
    label: str
    display_name: str = ""
    coords: tuple[float, float, float] | None = None


@dataclass
class HistoryVariable:
    """A variable in the history section.

    ``name_native`` is what the source called it (Abaqus ``U1``, Sesam
    ``DISPL_X``, Code_Aster ``DX``); ``name_canonical`` is the cross-
    solver equivalent the frontend can compare across analyses. For
    a v1 Abaqus-only bake the two are equal; later readers fill in
    the canonical mapping. ``group`` clusters related variables in
    the picker (e.g. ``U`` for the displacement triplet)."""

    name_native: str
    name_canonical: str
    category: FieldCategory = "other"
    component: str = ""
    group: str = ""
    unit: str = ""


@dataclass
class HistoryStep:
    """One source-side step. The history series rows are partitioned by
    step index because Abaqus restarts the frame clock at each step;
    sharing one x-axis across steps would misplace the samples."""

    i: int
    name: str
    procedure: str = ""
    domain: HistoryDomain = "time"


@dataclass
class HistorySeries:
    """A single time-series row: one (region, variable, step) tuple's
    samples. ``times`` and ``values`` line up index-for-index."""

    region_id: str
    variable: str
    step_idx: int
    times: list[float]
    values: list[float]


@dataclass
class HistoryRecords:
    """Bake-side container the reader hands back from
    ``try_history_records``. The bake serialises this into the
    manifest's ``history`` section verbatim."""

    regions: list[HistoryRegion] = dc_field(default_factory=list)
    variables: list[HistoryVariable] = dc_field(default_factory=list)
    steps: list[HistoryStep] = dc_field(default_factory=list)
    series: list[HistorySeries] = dc_field(default_factory=list)


class FEAStreamReader(Protocol):
    """Per-format streaming reader interface.

    The bake calls ``read_mesh_geometry`` once, then for each spec in
    ``field_specs`` calls ``iter_field_steps``. The reader is
    responsible for keeping any underlying file handle open across
    those calls.

    Element-field methods (``element_field_specs`` /
    ``iter_element_field_steps``) are optional: a reader that yields
    no element fields can return an empty list. The bake skips the
    element-field emission loop entirely when no specs come back.
    """

    def read_mesh_geometry(self) -> MeshGeometry: ...

    def field_specs(self) -> list[FieldSpec]: ...

    def iter_field_steps(self, field_name: str) -> Iterator[StepValues]: ...

    def element_field_specs(self) -> list[ElementFieldSpec]: ...

    def iter_element_field_steps(self, spec: ElementFieldSpec) -> Iterator[ElementStepValues]: ...

    def try_solid_beams(self) -> "SolidBeamMesh | None":
        """Optional: tessellate beam elements as 3D extruded solids.

        Readers that have section + axis info per beam element (SIF
        via the FEAResult adapter, future readers that carry similar
        metadata) return a :class:`SolidBeamMesh`. Readers without it
        (native RMED, FRD) return ``None`` — the bake then skips beam-
        solid emission and the manifest carries no ``beam_solids_url``.
        """
        ...

    def try_history_records(self) -> "HistoryRecords | None":
        """Optional: time-series history output at monitored points.

        Abaqus surfaces this via HistOutput (one row per sample); Sesam
        and Code_Aster have analogous concepts pending. Readers that
        have no history data return ``None`` and the manifest omits
        the ``history`` section. The bake also tolerates AttributeError
        from readers that pre-date this method.
        """
        ...

    def close(self) -> None: ...


def _ip_layout_from_int_positions(int_positions) -> list[dict]:
    """Best-effort IP-layout metadata for the frontend's layer + IP
    pickers. The Sesam reader populates ``int_positions`` from its
    ``INT_LOCATIONS`` table — a list of ``(ip_id, in_plane, layer)``
    tuples where ``layer`` is -0.5 for the bottom fibre, +0.5 for
    the top, 0 for mid. ``in_plane`` is either a corner-node index
    or a centroid-ish tuple. Other readers leave it as ``None``;
    return an empty list and let the frontend fall back to numeric
    IP indices.

    Output: one dict per integration point, in the original list
    order. Keys: ``ip`` (0-based), ``layer`` ("top" | "bottom" |
    "mid" | numeric string), ``in_plane`` (free-form string).
    """

    if not int_positions:
        return []

    layout: list[dict] = []
    for entry in int_positions:
        # Tolerate any reasonable tuple shape from the readers; keep
        # the metadata advisory rather than load-bearing.
        ip_id = None
        in_plane = None
        layer_val = None
        if isinstance(entry, (list, tuple)):
            if len(entry) >= 1:
                ip_id = entry[0]
            if len(entry) >= 2:
                in_plane = entry[1]
            if len(entry) >= 3:
                layer_val = entry[2]
        layer_label: str
        if isinstance(layer_val, (int, float)):
            lv = float(layer_val)
            if lv > 0:
                layer_label = "top"
            elif lv < 0:
                layer_label = "bottom"
            else:
                layer_label = "mid"
        else:
            layer_label = "mid"
        layout.append(
            {
                "ip": ip_id if isinstance(ip_id, int) else len(layout),
                "layer": layer_label,
                "in_plane": str(in_plane) if in_plane is not None else "",
            }
        )
    return layout


def _classify_field(name: str, sample) -> FieldCategory:
    """Best-effort field-category tag from the field name + the
    sample :class:`FieldData` payload.

    Two signals: an explicit ``field_type`` enum on the sample (only
    ``NodalFieldData`` carries one today — ``DISP`` / ``FORCE`` /
    ``VEL`` / ``UNKNOWN``) and the field name (e.g. Sesam RVNODDIS,
    RVSTRESS, RVFORCES). The frontend uses the category to decide
    whether to drive mesh deformation off a field; misclassification
    just means the user gets the warp toggle defaulted wrong, which
    they can fix in the UI. So the fallback is ``other``, not a hard
    failure.
    """

    from ada.fem.results.field_data import NodalFieldType

    field_type = getattr(sample, "field_type", None)
    if field_type is not None:
        if field_type == NodalFieldType.DISP:
            return "displacement"
        # NodalFieldType.FORCE on a nodal output is a reaction force
        # in every solver we currently read; if that ever stops being
        # true, the reader can override by passing category=... on
        # the spec construction directly.
        if field_type == NodalFieldType.FORCE:
            return "reaction"

    upper = name.upper()
    # Sesam RVNODDIS = nodal displacements; RVFORCES = beam-element
    # section forces (not nodal reactions); RVSTRESS = element
    # stresses. Code Aster: DEPL / SIEF / EPSI. Generic Abaqus /
    # MED: U / S / E.
    if any(token in upper for token in ("DISP", "DEPL", "RVNODDIS")):
        return "displacement"
    if any(token in upper for token in ("REAC", "RF", "RVFORCES")):
        return "reaction"
    if any(token in upper for token in ("STRESS", "SIGMA", "SIEF", "RVSTRESS")):
        return "stress"
    if any(token in upper for token in ("STRAIN", "EPSI", "EPS")):
        return "strain"
    return "other"


def _dedup_beam_tessellation(
    verts: np.ndarray,
    tris: np.ndarray,
    t_vals: np.ndarray,
    *,
    position_tolerance: float = 1e-6,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Collapse coincident-position vertices within a single beam's
    OCC tessellation.

    OCC tessellates each BRep face independently — side panels and
    end caps emit duplicate vertices at face boundaries. All such
    duplicates are at the same 3D position, the same axial position
    (so the same ``t``) and the same line element (so the same
    ``(n0, n1)``), which makes position-based merging lossless. The
    caller is responsible for NOT calling this across beam
    boundaries; that would corrupt AFBV at joints.

    Returns ``(unique_verts, remapped_tris, unique_t)`` where:

    * ``unique_verts`` is a deduplicated (n_unique, 3) array — the
      position from any merged buddy works since they're coincident.
    * ``remapped_tris`` has the same shape as the input ``tris`` but
      every vertex index now points into ``unique_verts``.
    * ``unique_t`` is the axial parameter for each merged vertex.
    """
    if verts.shape[0] == 0:
        return verts, tris.astype(np.uint32, copy=False), t_vals

    # Round to a fixed grid so floating-point jitter from the CAD kernel
    # doesn't cause coincident vertices to land in different buckets.
    rounded = np.round(verts / position_tolerance).astype(np.int64)
    _, inverse = np.unique(rounded, axis=0, return_inverse=True)
    n_unique = int(inverse.max()) + 1

    # Pick the first-encountered position for each bucket (or last
    # — they're coincident so it doesn't matter). Same for t.
    unique_verts = np.empty((n_unique, 3), dtype=verts.dtype)
    unique_verts[inverse] = verts

    unique_t = np.empty(n_unique, dtype=t_vals.dtype)
    unique_t[inverse] = t_vals

    remapped = inverse[tris].astype(np.uint32, copy=False)
    return unique_verts, remapped, unique_t


def tessellate_beams_to_solid_mesh(
    beams,
    *,
    extra_skip_reasons: dict | None = None,
    total_beams: int | None = None,
) -> "SolidBeamMesh | None":
    """Run OCC tessellation over a list of beams and produce a SolidBeamMesh.

    Each ``beams`` entry is a ``(beam, elem_id, n0_idx, n1_idx, n0_pos, n1_pos)``
    tuple where ``beam`` is a fully-constructed :class:`ada.Beam` (its
    ``section``, ``material``, ``up``, and endpoints supply everything OCC
    needs), ``elem_id`` is the source line-element id used as the per-beam
    label in the AFEL element-range table, ``n0_idx`` / ``n1_idx`` are the
    0-based positions of the parent line-element's endpoint nodes in the
    bake's main point buffer (used by the AFBV warp sidecar to lerp
    displacement onto the solid surface), and ``n0_pos`` / ``n1_pos`` are
    the world-space coordinates of those endpoints (used to compute the
    axial parameter ``t`` per vertex).

    Returns ``None`` when zero beams successfully tessellated — the bake
    then omits the beam-solids artefacts from the manifest.

    Per-beam failures are bucketed by reason into ``skip_reasons`` and
    logged once as a summary; the offending beam is dropped from the
    output rather than aborting the whole bake.

    Callers that pre-filter beams (e.g. ``no-section`` or
    ``genbeam-no-profile`` cases the reader can detect cheaply before
    OCC sees them) can pass those counts in via ``extra_skip_reasons``
    so the coverage summary reflects the full picture; ``total_beams``
    overrides the auto-default of ``len(beams)`` for the same reason.
    """

    from collections import defaultdict

    from ada.config import get_logger
    from ada.occ.tessellating import BatchTessellator
    from ada.visit.rendering.femviz import ElementRange

    bt = BatchTessellator()

    all_positions: list[np.ndarray] = []
    all_indices: list[np.ndarray] = []
    all_n0: list[np.ndarray] = []
    all_n1: list[np.ndarray] = []
    all_t: list[np.ndarray] = []
    ranges: list[ElementRange] = []
    vertex_offset = 0
    tri_cursor = 0
    skip_reasons: dict[str, int] = defaultdict(int)
    if extra_skip_reasons:
        for k, v in extra_skip_reasons.items():
            skip_reasons[k] += int(v)
    if total_beams is None:
        total_beams = len(beams) + sum(skip_reasons.values())
    success_count = 0

    for beam, elem_id, n0_idx, n1_idx, n0_pos, n1_pos in beams:
        try:
            geom = beam.solid_geom()
            ms = bt.tessellate_geom(geom, beam)
        except Exception as e:  # noqa: BLE001 — defensive
            skip_reasons[f"occ-error[{type(e).__name__}]"] += 1
            get_logger().debug(
                "beam-solid OCC failure elem %s: %s",
                elem_id,
                e,
            )
            continue

        pos = getattr(ms, "position", None)
        idx = getattr(ms, "indices", None)
        if pos is None or idx is None or pos.size == 0 or idx.size == 0:
            skip_reasons["empty-tessellation"] += 1
            continue

        verts_raw = np.asarray(pos, dtype=np.float64).reshape(-1, 3)
        tris_local = np.asarray(idx, dtype=np.uint32).reshape(-1, 3)

        p0 = np.asarray(n0_pos, dtype=np.float64)
        p1 = np.asarray(n1_pos, dtype=np.float64)
        axis = p1 - p0
        axis_sq = float(np.dot(axis, axis))
        if axis_sq <= 0:
            # Zero-length beam: every vertex t=0 so disp collapses to disp[n0].
            t_vals_raw = np.zeros(verts_raw.shape[0], dtype=np.float32)
        else:
            rel = verts_raw - p0
            t_vals_raw = np.clip(rel @ axis / axis_sq, 0.0, 1.0).astype(np.float32)

        verts, tris_local_dedup, t_vals = _dedup_beam_tessellation(
            verts_raw,
            tris_local,
            t_vals_raw,
        )
        tris = tris_local_dedup + vertex_offset

        all_positions.append(verts)
        all_indices.append(tris.astype(np.uint32, copy=False))
        all_n0.append(np.full(verts.shape[0], n0_idx, dtype=np.uint32))
        all_n1.append(np.full(verts.shape[0], n1_idx, dtype=np.uint32))
        all_t.append(t_vals)

        tri_count = int(tris.shape[0])
        ranges.append(
            ElementRange(
                label=int(elem_id),
                tri_start=tri_cursor,
                tri_count=tri_count,
            )
        )
        vertex_offset += int(verts.shape[0])
        tri_cursor += tri_count
        success_count += 1

    if total_beams:
        skip_summary = ", ".join(f"{k}={v}" for k, v in sorted(skip_reasons.items())) or "none"
        get_logger().info(
            "beam-solid coverage: %d of %d beams tessellated (skip: %s)",
            success_count,
            total_beams,
            skip_summary,
        )

    if not all_positions:
        return None

    return SolidBeamMesh(
        points=np.concatenate(all_positions, axis=0),
        triangles=np.concatenate(all_indices, axis=0),
        element_ranges=ranges,
        vertex_node0=np.concatenate(all_n0, axis=0),
        vertex_node1=np.concatenate(all_n1, axis=0),
        vertex_t=np.concatenate(all_t, axis=0),
        total_beams=total_beams,
        skip_reasons=dict(skip_reasons),
    )


class FEAResultStreamAdapter:
    """Wrap an in-memory :class:`FEAResult` so the bake can consume it
    through the streaming reader interface.

    No real streaming benefit — the adapter already has the full
    FEAResult — but it lets formats that haven't been rewritten as
    native streamers (SIF, FRD) flow through the same artefact
    pipeline. When a big-model SIF or FRD case actually OOMs the
    bake, the answer is to write a native streaming reader for that
    format and replace the adapter for that format only; the
    artefact code on top doesn't change.
    """

    def __init__(self, result):
        self._result = result
        self._geom: MeshGeometry | None = None
        self._field_specs: list[FieldSpec] | None = None
        self._elem_field_specs: list[ElementFieldSpec] | None = None
        # Optional FEA input concepts (masses / BCs / load scenarios) as a manifest-shaped
        # dict. SIF/SIN leave this None; the FEM reader (_make_fem_reader) scrapes it from the
        # deck so the Scene > FEM panel can draw the glyph overlay.
        self._fem_concepts: dict | None = None
        # Optional FEM node/element sets as manifest group dicts ({name, members, fe_object_type}).
        # Populated by the FEM reader so the Scene > FEM groups picker works for design models.
        self._groups: list[dict] | None = None

        # Remap real node IDs → 0-based point indices. ElementBlock
        # stores arbitrary-id node references (1-based for RMED,
        # arbitrary for SIF/FRD); the artefact pipeline expects
        # 0-based indices into the points array.
        ids = result.mesh.nodes.identifiers
        self._nmap = {int(x): i for i, x in enumerate(ids)}

    def try_fem_concepts(self) -> dict | None:
        return self._fem_concepts

    def try_groups(self) -> list[dict] | None:
        return self._groups

    # ----- protocol -------------------------------------------------------

    def read_mesh_geometry(self) -> MeshGeometry:
        if self._geom is not None:
            return self._geom

        from ada.fem.shapes.mesh_types import ada_to_str_type

        points = np.asarray(self._result.mesh.nodes.coords, dtype=np.float64)
        if points.ndim == 2 and points.shape[1] == 2:
            points = np.column_stack([points, np.zeros(points.shape[0])])

        cell_blocks: list[CellBlockData] = []
        for block in self._result.mesh.elements:
            cell_type_str = ada_to_str_type.get(block.elem_info.type)
            if cell_type_str is None:
                # Unsupported element type for visualisation — skip
                # rather than crash, matching the legacy GLB pipeline's
                # posture. Mesh GLB just gets fewer faces.
                continue

            flat = np.asarray(block.node_refs).reshape(-1)
            try:
                data_0 = np.fromiter(
                    (self._nmap[int(x)] for x in flat),
                    dtype=np.int64,
                    count=flat.size,
                ).reshape(block.node_refs.shape)
            except KeyError as e:
                # Element references a node that isn't in the mesh;
                # surface explicitly so the source data error is
                # obvious.
                raise ValueError(
                    f"Element block of type {cell_type_str!r} references " f"unknown node id {e.args[0]}."
                ) from None
            # ElementBlock.identifiers is the per-element label as it
            # appeared in the source FEA file. Forward verbatim so the
            # selection sidecar can carry real labels back to the
            # picker, not just iteration-order indices.
            block_ids = getattr(block, "identifiers", None)
            if block_ids is not None:
                identifiers = np.asarray(block_ids, dtype=np.int64).reshape(-1)
                if identifiers.shape[0] != data_0.shape[0]:
                    # Defensive: if a reader produces a length mismatch
                    # we'd silently misattribute labels; surface it.
                    raise ValueError(
                        f"ElementBlock.identifiers length {identifiers.shape[0]} "
                        f"!= n_cells {data_0.shape[0]} for {cell_type_str!r}."
                    )
            else:
                identifiers = None
            cell_blocks.append(CellBlockData(cell_type=cell_type_str, data=data_0, identifiers=identifiers))

        self._geom = MeshGeometry(points=points, cell_blocks=cell_blocks)
        return self._geom

    def field_specs(self) -> list[FieldSpec]:
        if self._field_specs is not None:
            return self._field_specs

        from ada.fem.results.field_data import (
            ElementFieldData,
            FieldPosition,
            NodalFieldData,
        )

        n_points = int(self._result.mesh.nodes.coords.shape[0])
        specs: list[FieldSpec] = []

        for name, results in self._result.get_results_grouped_by_field_value().items():
            if not results:
                continue
            sorted_results = sorted(results, key=lambda r: r.step)
            first = sorted_results[0]

            if isinstance(first, NodalFieldData):
                support = "nodal"
            elif isinstance(first, ElementFieldData):
                if first.field_pos == FieldPosition.NODAL:
                    support = "element_nodal"
                else:
                    support = "gauss"
            else:
                continue

            # Step value semantics: eigen analysis stores the
            # frequency in eigen_freq and uses .step as a 1-based
            # index. Static analysis stores the time directly in
            # .step. The picker just wants a monotonic label per
            # step, so either works.
            step_values = [float(r.eigen_freq if r.eigen_freq is not None else r.step) for r in sorted_results]
            components = list(first.components) or [first.name]

            specs.append(
                FieldSpec(
                    name=name,
                    components=components,
                    n_steps=len(sorted_results),
                    n_points=n_points,
                    support=support,
                    step_values=step_values,
                    category=_classify_field(name, first),
                )
            )

        self._field_specs = specs
        return specs

    def iter_field_steps(self, field_name: str):
        spec = next((s for s in self.field_specs() if s.name == field_name), None)
        if spec is None:
            raise KeyError(field_name)
        if spec.support != "nodal":
            raise NotImplementedError(
                f"streaming non-nodal field {field_name!r} (support={spec.support}) "
                f"not implemented via iter_field_steps; use iter_element_field_steps"
            )

        results = self._result.get_results_grouped_by_field_value().get(field_name, [])
        sorted_results = sorted(results, key=lambda r: r.step)
        for i, r in enumerate(sorted_results):
            arr = np.asarray(r.get_all_values())
            yield StepValues(
                step_index=i,
                step_value=spec.step_values[i],
                values=arr,
            )

    def _grouped_element_fields(self):
        """Group ``ElementFieldData`` rows by ``(name, elem_type)``.

        Cached as ``self._elem_field_groups`` so callers (specs +
        iterators) walk the FEAResult.results list once. Skip
        ElementFieldData with an unknown elem_type — the GLB has no
        geometry for it so colouring it would have nowhere to land."""

        from collections import defaultdict

        from ada.fem.results.field_data import ElementFieldData
        from ada.fem.shapes.mesh_types import ada_to_str_type

        cached = getattr(self, "_elem_field_groups", None)
        if cached is not None:
            return cached

        grouped: dict[tuple[str, str], list] = defaultdict(list)
        for r in self._result.results:
            if not isinstance(r, ElementFieldData):
                continue
            if r.elem_type is None:
                continue
            elem_type_str = ada_to_str_type.get(r.elem_type)
            if elem_type_str is None:
                continue
            grouped[(r.name, elem_type_str)].append(r)

        self._elem_field_groups = grouped
        return grouped

    def element_field_specs(self) -> list[ElementFieldSpec]:
        if self._elem_field_specs is not None:
            return self._elem_field_specs

        specs: list[ElementFieldSpec] = []
        for (name, elem_type_str), items in self._grouped_element_fields().items():
            sorted_items = sorted(items, key=lambda r: r.step)
            first = sorted_items[0]
            vals = np.asarray(first.values)
            if vals.ndim != 2 or vals.shape[1] < 2 + len(first.components):
                # Row layout is (elem_label, ip_index, *component_values);
                # surface the shape mismatch rather than silently mis-baking.
                raise ValueError(
                    f"element field {name!r} ({elem_type_str}) has unexpected "
                    f"values shape {vals.shape}; expected (n_rows, "
                    f">= 2 + {len(first.components)})."
                )

            ip_indices = vals[:, 1].astype(int)
            if ip_indices.size == 0:
                continue
            n_ips = int(ip_indices.max())
            if n_ips <= 0:
                # IP indices are 1-based in the SIF reader; a non-positive
                # max means the source data is malformed for this field.
                raise ValueError(
                    f"element field {name!r} ({elem_type_str}) has non-positive " f"IP indices; cannot determine n_ips."
                )
            if vals.shape[0] % n_ips != 0:
                raise ValueError(
                    f"element field {name!r} ({elem_type_str}) row count "
                    f"{vals.shape[0]} is not a multiple of n_ips={n_ips}; "
                    f"likely a ragged IP layout the bake doesn't yet handle."
                )
            n_elements = vals.shape[0] // n_ips
            # Element labels appear once per element (we stride by n_ips
            # through col 0). Row order from the reader becomes the
            # spec's canonical order — manifest carries it so the
            # frontend can map ``label → bucket index``.
            labels = vals[::n_ips, 0].astype(int).tolist()

            step_values = [float(r.eigen_freq if r.eigen_freq is not None else r.step) for r in sorted_items]
            ip_layout = _ip_layout_from_int_positions(getattr(first, "int_positions", None))

            specs.append(
                ElementFieldSpec(
                    name=name,
                    components=list(first.components),
                    n_steps=len(sorted_items),
                    elem_type=elem_type_str,
                    n_elements=n_elements,
                    n_ips=n_ips,
                    element_labels=labels,
                    step_values=step_values,
                    ip_layout=ip_layout,
                    category=_classify_field(name, first),
                    support="gauss",
                )
            )

        self._elem_field_specs = specs
        return specs

    def iter_element_field_steps(self, spec: ElementFieldSpec):
        from ada.fem.results.field_data import ElementFieldData  # noqa: F401

        items = self._grouped_element_fields().get((spec.name, spec.elem_type))
        if not items:
            raise KeyError((spec.name, spec.elem_type))
        sorted_items = sorted(items, key=lambda r: r.step)
        if len(sorted_items) != spec.n_steps:
            raise ValueError(
                f"element field {spec.name!r} step-count drift: spec says "
                f"{spec.n_steps}, found {len(sorted_items)}."
            )

        n_components = len(spec.components)
        for i, r in enumerate(sorted_items):
            vals = np.asarray(r.values, dtype=np.float32)
            if vals.shape != (spec.n_elements * spec.n_ips, vals.shape[1]):
                raise ValueError(
                    f"element field {spec.name!r} step {r.step} has shape "
                    f"{vals.shape}; expected ({spec.n_elements * spec.n_ips}, ...)."
                )
            per_elem = vals.reshape(spec.n_elements, spec.n_ips, -1)
            # First 2 columns are (elem_label, ip_index); strip them.
            comp_vals = per_elem[:, :, 2 : 2 + n_components]
            # Verify label alignment with the spec's canonical order —
            # readers that emit rows in different orders between steps
            # would silently mis-correlate.
            step_labels = per_elem[:, 0, 0].astype(int).tolist()
            if step_labels != spec.element_labels:
                raise ValueError(
                    f"element field {spec.name!r} step {r.step} label order "
                    f"differs from spec; reader yielded different element "
                    f"order between steps."
                )
            yield ElementStepValues(
                step_index=i,
                step_value=spec.step_values[i],
                values=np.ascontiguousarray(comp_vals, dtype=np.float32),
            )

    def try_solid_beams(self) -> "SolidBeamMesh | None":
        """Tessellate each beam (line) element as a 3D extruded section
        via OCC and merge into a single vertex+index buffer with
        per-beam draw ranges.

        Requires the wrapped FEAResult.mesh to carry sections +
        materials + vectors + elem_data (the SIF reader populates all
        four; RMED native does not). Returns ``None`` when any of
        those is missing — the bake then skips solid-beam emission.

        Individual beam tessellation failures (bad section, OCC blow-
        up) are logged and the offending beam is omitted from the
        output rather than failing the whole bake. Empty result →
        return ``None`` so the manifest doesn't carry a zero-element
        sidecar.
        """

        mesh = self._result.mesh
        if not getattr(mesh, "sections", None):
            return None
        if mesh.elem_data is None:
            return None

        from ada import Part
        from ada.fem.formats.utils import line_elem_to_beam

        line_elems = mesh.get_line_elems()
        if not line_elems:
            return None

        dummy_part = Part(self._result.name or "solid_beams")
        beams: list = []
        # Source-side pre-filter — these reject reasons are cheap to
        # detect before OCC sees the geometry. Bucketing them by
        # category here keeps the bake's coverage summary informative.
        extra_skip: dict[str, int] = {}
        for elem in line_elems:
            n0_node = elem.nodes[0]
            n1_node = elem.nodes[-1]
            try:
                n0_idx = self._nmap[int(n0_node.id)]
                n1_idx = self._nmap[int(n1_node.id)]
            except KeyError:
                extra_skip["endpoint-not-in-mesh"] = extra_skip.get("endpoint-not-in-mesh", 0) + 1
                continue

            sec = elem.fem_sec.section if elem.fem_sec is not None else None
            if sec is None:
                extra_skip["no-section"] = extra_skip.get("no-section", 0) + 1
                continue
            if getattr(sec, "type", None) == "GENBEAM":
                # Generic-cross-section beams carry property numbers
                # only (A, Iy, Iz, …) — no geometric profile to
                # extrude. Most common gap in Sesam models.
                extra_skip["genbeam-no-profile"] = extra_skip.get("genbeam-no-profile", 0) + 1
                continue
            if elem.fem_sec.local_z is None:
                extra_skip["missing-local-z"] = extra_skip.get("missing-local-z", 0) + 1
                continue

            beam = line_elem_to_beam(elem, dummy_part, "BM")
            beams.append((beam, int(elem.id), n0_idx, n1_idx, n0_node.p, n1_node.p))

        return tessellate_beams_to_solid_mesh(
            beams,
            extra_skip_reasons=extra_skip,
            total_beams=len(line_elems),
        )

    def close(self) -> None:
        pass

    def __enter__(self) -> "FEAResultStreamAdapter":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


@dataclass
class FieldArtefactMeta:
    """Bake output per field — used to compose the manifest entry."""

    spec: FieldSpec
    blob_filename: str
    stride_bytes: int
    scalar_range_per_component: dict[str, tuple[float, float]]
    scalar_range_magnitude: tuple[float, float]


# ---------------------------------------------------------------------------
# Mesh GLB writer
# ---------------------------------------------------------------------------


def _compute_topology(geom: MeshGeometry):
    """Compute edges/faces/element-ranges from the geometry.

    Wraps :func:`get_mesh_topology` so the bake walks the per-element
    ``ElemShape`` machinery exactly once per source instead of once
    per writer.
    """

    from ada.fem.results.common import MeshData
    from ada.visit.rendering.femviz import get_mesh_topology

    mesh_data = MeshData(points=geom.points, cells=geom.cell_blocks)
    return get_mesh_topology(mesh_data)


def write_mesh_glb(geom: MeshGeometry, out_path: os.PathLike, *, faces=None) -> None:
    """Write a geometry-only GLB (vertices + face indices, no per-step
    or per-vertex data baked in). The frontend renders edges from the
    face topology via a wireframe pass; the legacy GLB pipeline still
    emits explicit edge geometry, but the streaming path doesn't need
    to.

    ``faces`` may be supplied by callers that have already computed
    the topology; when omitted, the function recomputes from
    ``geom``. Standalone-test callers leave it unset; the bake passes
    the precomputed list to avoid re-walking the mesh."""

    import trimesh
    from trimesh.visual.material import PBRMaterial

    if faces is None:
        faces = _compute_topology(geom).faces

    out_path = pathlib.Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    scene = trimesh.Scene()
    if faces:
        face_arr = np.asarray(faces, dtype=np.uint32).reshape(-1, 3)
        face_mesh = trimesh.Trimesh(vertices=geom.points, faces=face_arr, process=False)
        face_mesh.visual.material = PBRMaterial(doubleSided=True)
        scene.add_geometry(face_mesh, node_name="mesh", geom_name="faces")
    else:
        # Line-only models (beam fixtures): no faces. We still emit a
        # GLB so the manifest's mesh.url resolves; trimesh refuses to
        # export an empty scene, so seed it with a degenerate point
        # cloud at the bbox centre. The frontend treats face-less GLBs
        # via the line-element render path.
        empty = trimesh.PointCloud(vertices=geom.points)
        scene.add_geometry(empty, node_name="mesh", geom_name="points")

    with open(out_path, "wb") as f:
        scene.export(file_obj=f, file_type="glb")


# ---------------------------------------------------------------------------
# Mesh-edge sidecar writer
# ---------------------------------------------------------------------------


def write_mesh_edges(geom: MeshGeometry, out_path: os.PathLike, *, edges=None) -> int:
    """Write the per-element edges as a deduped uint32 pair list.

    Edges come from each cell's :class:`ElemShape` directly — they
    reflect the *element* boundaries, not the artefact diagonals
    introduced by triangulating quad faces. The frontend renders
    these as a wireframe overlay so users see actual element
    topology, which matters for higher-order or quad-faced cells
    where the visual triangulation would draw misleading edges.

    Adjacent solid elements share edges; we sort each pair and
    np.unique-dedupe so a typical hex mesh ends up with roughly half
    the line count.

    ``edges`` may be passed by callers that have already computed the
    topology; otherwise the function recomputes.
    """

    if edges is None:
        edges = _compute_topology(geom).edges

    if edges:
        edge_pairs = np.asarray(edges, dtype=np.uint32).reshape(-1, 2)
        sorted_pairs = np.sort(edge_pairs, axis=1)
        unique = np.unique(sorted_pairs, axis=0)
        n_edges = int(unique.shape[0])
        payload = unique.astype(np.uint32).tobytes(order="C")
    else:
        n_edges = 0
        payload = b""

    out_path = pathlib.Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "wb") as f:
        prefix = EDGE_MAGIC + struct.pack("<II", EDGE_VERSION, n_edges)
        f.write(prefix + b"\x00" * (EDGE_HEADER_BYTES - len(prefix)))
        f.write(payload)
    return n_edges


# ---------------------------------------------------------------------------
# Mesh-element sidecar writer
# ---------------------------------------------------------------------------


def write_mesh_elements(
    geom: MeshGeometry,
    out_path: os.PathLike,
    *,
    element_ranges=None,
) -> int:
    """Write per-element ``(label, tri_start, tri_count)`` ranges into
    the AFEM sidecar.

    Frontend turns these into ``userdata.id_hierarchy`` and
    ``userdata.draw_ranges_<meshName>`` so the FEA mesh slots into
    the existing CustomBatchedMesh pick + highlight pipeline. Labels
    are uint32; an element id larger than ``2**32 - 1`` would be
    truncated, so the writer raises rather than silently aliasing.

    ``element_ranges`` may be passed by callers that have already
    computed the topology; otherwise the function recomputes.
    """

    if element_ranges is None:
        element_ranges = _compute_topology(geom).element_ranges

    n_elements = len(element_ranges)
    out_path = pathlib.Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with open(out_path, "wb") as f:
        prefix = ELEM_MAGIC + struct.pack("<II", ELEM_VERSION, n_elements)
        f.write(prefix + b"\x00" * (ELEM_HEADER_BYTES - len(prefix)))

        if n_elements:
            arr = np.empty((n_elements, 3), dtype=np.uint32)
            for i, er in enumerate(element_ranges):
                if er.label < 0 or er.label >= 2**32:
                    raise ValueError(
                        f"AFEM label {er.label} for element index {i} doesn't "
                        f"fit in uint32; widen the format or normalise labels."
                    )
                arr[i, 0] = er.label
                arr[i, 1] = er.tri_start
                arr[i, 2] = er.tri_count
            f.write(arr.tobytes(order="C"))

    return n_elements


# ---------------------------------------------------------------------------
# Beam-solid mesh + sidecar writers
# ---------------------------------------------------------------------------


def write_beam_solids_glb(mesh: SolidBeamMesh, out_path: os.PathLike) -> None:
    """Write the concatenated beam-solid mesh as a geometry-only GLB.

    Same shape as :func:`write_mesh_glb` but takes an explicit
    ``(points, triangles)`` rather than going through
    :func:`_compute_topology`. ``trimesh.Trimesh(process=False)`` —
    skipping process is critical, otherwise trimesh merges duplicate
    vertices and the per-beam draw ranges go stale.
    """

    import trimesh
    from trimesh.visual.material import PBRMaterial

    out_path = pathlib.Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    face_arr = np.asarray(mesh.triangles, dtype=np.uint32).reshape(-1, 3)
    tm = trimesh.Trimesh(vertices=mesh.points, faces=face_arr, process=False)
    tm.visual.material = PBRMaterial(doubleSided=True)
    scene = trimesh.Scene()
    scene.add_geometry(tm, node_name="beam_solids", geom_name="faces")
    with open(out_path, "wb") as f:
        scene.export(file_obj=f, file_type="glb")


def write_beam_solids_warp(mesh: SolidBeamMesh, out_path: os.PathLike) -> int:
    """Write the AFBV sidecar: per-vertex (node0_idx, node1_idx, t).

    Frontend reads these once at load and computes the beam-solid
    mesh's morph delta on every apply as
    ``lerp(disp[node0], disp[node1], t)`` per vertex. Result is the
    parent beam's two endpoints driving every vertex along the beam,
    so a scaled deformation keeps the solid mesh connected to the
    rest of the structure at the endpoints.
    """

    out_path = pathlib.Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    n0 = np.asarray(mesh.vertex_node0, dtype=np.uint32)
    n1 = np.asarray(mesh.vertex_node1, dtype=np.uint32)
    t = np.asarray(mesh.vertex_t, dtype=np.float32)
    n_verts = int(n0.shape[0])
    if not (n0.shape == n1.shape == t.shape):
        raise ValueError(f"AFBV shape mismatch: n0={n0.shape}, n1={n1.shape}, t={t.shape}")

    with open(out_path, "wb") as f:
        prefix = BEAM_WARP_MAGIC + struct.pack("<II", BEAM_WARP_VERSION, n_verts)
        f.write(prefix + b"\x00" * (BEAM_WARP_HEADER_BYTES - len(prefix)))
        if n_verts:
            # Interleaved layout: one (n0, n1, t) record per vertex —
            # frontend reads three typed arrays from one fetch by
            # striding into the same ArrayBuffer at the right offsets.
            payload = np.empty(n_verts * 3, dtype=np.uint32)
            payload[0::3] = n0
            payload[1::3] = n1
            # ``t`` is float32 but the underlying bits land in the
            # uint32 slot — view-cast keeps the float bit pattern.
            payload[2::3] = t.view(np.uint32)
            f.write(payload.tobytes(order="C"))
    return n_verts


def write_beam_solids_elements(mesh: SolidBeamMesh, out_path: os.PathLike) -> int:
    """Write the per-beam ``(label, tri_start, tri_count)`` sidecar in
    the AFEM format. Same magic + version as the main-mesh elements
    sidecar so the frontend's existing :func:`parseMeshElements`
    parser reads it without modification.
    """

    return write_mesh_elements(
        # Geometry-only wrapper just so we satisfy the existing
        # write_mesh_elements signature; element_ranges is the
        # actually-used field. The geom argument is ignored when
        # element_ranges is passed in directly.
        MeshGeometry(points=mesh.points, cell_blocks=[]),
        out_path,
        element_ranges=mesh.element_ranges,
    )


def write_beam_solids_edges(
    mesh: SolidBeamMesh,
    out_path: os.PathLike,
    *,
    position_tolerance: float = 1e-6,
) -> int:
    """Write the beam-solid element-boundary edges as AFEG.

    The triangulated beam-solid mesh has no inherent line topology —
    each beam is an extruded cross-section, every triangle's three
    edges look identical to the wireframe pass. Without separating
    "internal triangulation diagonal" from "this is where one beam
    element ends and the next begins" we'd either draw all 3N edges
    (visual mush) or none (the current state — beams look like one
    continuous tube).

    The element ranges from the AFEM sidecar already tell us which
    triangle belongs to which line-element label. So an edge is a
    boundary edge if either:

    * Only one triangle uses it (true mesh perimeter — open beam
      ends or genuinely free edges).
    * Two-or-more triangles use it but they live in different
      elements (the seam between adjacent beam-elements along the
      axis).

    Interior edges (multiple triangles, all in the same element) are
    dropped — those are the triangulation artefacts of the extrusion.

    **Edges are keyed by 3D position, not vertex index.** OCC
    tessellates each FACE of a solid independently and each beam
    independently, so the side panel and end cap of a single beam
    have different vertex indices at the same 3D positions. An
    index-based comparison would treat their shared edge as a
    one-triangle boundary edge on each side and draw the cross-
    hatching artefacts the user sees. Position-bucketing collapses
    coincident vertices so within-beam face seams correctly resolve
    as same-element interior edges and get dropped. Beam-to-beam
    joints (where two elements share node positions) still survive
    because their bucket-edges have triangles from different
    elements.

    Output is the AFEG format already used by the main mesh wireframe,
    so the frontend wires up the same :class:`THREE.LineSegments`
    sharing the beam-solid's position + morph attributes.
    """

    out_path = pathlib.Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    tris = np.asarray(mesh.triangles, dtype=np.uint32).reshape(-1, 3)
    n_tris = int(tris.shape[0])

    if n_tris == 0 or not mesh.element_ranges:
        with open(out_path, "wb") as f:
            prefix = EDGE_MAGIC + struct.pack("<II", EDGE_VERSION, 0)
            f.write(prefix + b"\x00" * (EDGE_HEADER_BYTES - len(prefix)))
        return 0

    # Per-triangle element label. Triangles outside any explicit range
    # are left at a sentinel so an edge that bridges "labeled" and
    # "unlabeled" still counts as a boundary — defensive against a
    # reader that ships partial coverage.
    tri_label = np.full(n_tris, np.iinfo(np.int64).max, dtype=np.int64)
    for er in mesh.element_ranges:
        if er.tri_count <= 0:
            continue
        s = int(er.tri_start)
        e = s + int(er.tri_count)
        tri_label[s:e] = int(er.label)

    # Bucket vertices by rounded 3D position so coincident vertices
    # from independent OCC face tessellations (within one beam) or
    # from adjacent beams at a shared joint resolve to the same
    # bucket id. ``np.unique(axis=0, return_inverse=True)`` returns a
    # deterministic mapping from row → group id sized to the number
    # of unique rows; we use the inverse as our bucket assignment.
    points = np.asarray(mesh.points, dtype=np.float64).reshape(-1, 3)
    rounded = np.round(points / position_tolerance).astype(np.int64)
    _, bucket_id = np.unique(rounded, axis=0, return_inverse=True)
    bucket_id = bucket_id.astype(np.uint64)

    # Three edges per triangle. Original vertex indices kept for the
    # final write so the frontend's LineSegments indexes into the
    # beam-solid GLB's existing position attribute. Bucket pairs are
    # used only for the grouping/dedup pass.
    e01 = np.stack([tris[:, 0], tris[:, 1]], axis=1)
    e12 = np.stack([tris[:, 1], tris[:, 2]], axis=1)
    e20 = np.stack([tris[:, 2], tris[:, 0]], axis=1)
    edges = np.concatenate([e01, e12, e20], axis=0)
    edges_sorted = np.sort(edges, axis=1)
    edge_labels = np.tile(tri_label, 3)

    # Bucket-based edge key: (min_bucket, max_bucket) packed into a
    # single uint64. Bucket ids fit in 32 bits unless the mesh has
    # ≥ 2^32 unique vertex positions (it doesn't).
    bucket_edges = np.empty_like(edges, dtype=np.uint64)
    bucket_edges[:, 0] = bucket_id[edges[:, 0]]
    bucket_edges[:, 1] = bucket_id[edges[:, 1]]
    bucket_edges_sorted = np.sort(bucket_edges, axis=1)
    key = (bucket_edges_sorted[:, 0] << np.uint64(32)) | bucket_edges_sorted[:, 1]
    order = np.argsort(key, kind="stable")
    key_sorted = key[order]
    labels_sorted = edge_labels[order]
    edges_sorted_by_key = edges_sorted[order]

    # Group boundaries: a unique edge spans key_sorted[start:next_start].
    is_new_group = np.empty(key_sorted.shape[0], dtype=bool)
    is_new_group[0] = True
    is_new_group[1:] = key_sorted[1:] != key_sorted[:-1]
    group_starts = np.flatnonzero(is_new_group)
    # Append n so np.diff gives the size of the final group.
    group_starts_ext = np.concatenate([group_starts, np.array([key_sorted.shape[0]], dtype=group_starts.dtype)])
    group_sizes = np.diff(group_starts_ext)

    # Vectorized "does any label in this group differ from the first?":
    # repeat the first label across each group, compare to the per-row
    # label, then reduce by sum per group.
    first_label_per_row = np.repeat(labels_sorted[group_starts], group_sizes)
    differs = labels_sorted != first_label_per_row
    diff_per_group = np.add.reduceat(differs.astype(np.int64), group_starts)

    # Keep edges that are either mesh-boundary (one triangle) or span
    # two elements (some label in the group differs from the first).
    keep_mask = (group_sizes == 1) | (diff_per_group > 0)
    if not np.any(keep_mask):
        kept_pairs = np.empty((0, 2), dtype=np.uint32)
    else:
        kept_pairs = edges_sorted_by_key[group_starts[keep_mask]].astype(np.uint32)

    n_edges = int(kept_pairs.shape[0])
    payload = kept_pairs.tobytes(order="C") if n_edges else b""

    with open(out_path, "wb") as f:
        prefix = EDGE_MAGIC + struct.pack("<II", EDGE_VERSION, n_edges)
        f.write(prefix + b"\x00" * (EDGE_HEADER_BYTES - len(prefix)))
        f.write(payload)
    return n_edges


# ---------------------------------------------------------------------------
# Field blob writer (streaming)
# ---------------------------------------------------------------------------


def _encode_blob_header(spec: FieldSpec, stride_bytes: int) -> bytes:
    """Pack the JSON header + binary frame into the fixed-size prefix.

    Header carries only O(1) binary shape metadata — n_steps,
    n_points, n_components, dtype, stride. Per-step labels / time
    values / scalar ranges all live in the manifest, where they
    belong. This keeps the binary header well under 1 KB no matter
    how many steps the field has, so the frontend can rely on a
    fixed 1 KB initial range read.
    """

    header_obj = {
        "name": spec.name,
        "n_steps": spec.n_steps,
        "n_points": spec.n_points,
        "n_components": spec.n_components,
        "dtype": spec.dtype.name,
        "stride_bytes": stride_bytes,
    }
    json_bytes = json.dumps(header_obj, separators=(",", ":")).encode("utf-8")
    if 12 + len(json_bytes) > BLOB_HEADER_BYTES:
        raise ValueError(
            f"Blob header for field {spec.name!r} doesn't fit in "
            f"{BLOB_HEADER_BYTES} bytes (needs {12 + len(json_bytes)})."
        )
    prefix = BLOB_MAGIC + struct.pack("<II", BLOB_VERSION, len(json_bytes)) + json_bytes
    return prefix + b"\x00" * (BLOB_HEADER_BYTES - len(prefix))


def write_field_blob_streaming(
    reader: FEAStreamReader,
    spec: FieldSpec,
    out_path: os.PathLike,
) -> FieldArtefactMeta:
    """Stream one field's step-stack to disk; return the manifest meta.

    Computes the per-component and magnitude scalar ranges as steps
    pass through, so the bake never needs the full field stack in
    memory.
    """

    out_path = pathlib.Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    stride = spec.n_points * spec.n_components * spec.dtype.itemsize

    comp_min = np.full(spec.n_components, np.inf, dtype=np.float64)
    comp_max = np.full(spec.n_components, -np.inf, dtype=np.float64)
    mag_min = np.inf
    mag_max = -np.inf

    with open(out_path, "wb") as f:
        f.write(_encode_blob_header(spec, stride))
        seen = 0
        for sv in reader.iter_field_steps(spec.name):
            arr = np.asarray(sv.values, dtype=spec.dtype)
            if arr.ndim == 1:
                arr = arr.reshape(-1, 1)
            if arr.shape != (spec.n_points, spec.n_components):
                raise ValueError(
                    f"Field {spec.name!r} step {sv.step_index} produced shape "
                    f"{arr.shape}, expected {(spec.n_points, spec.n_components)}."
                )
            f.write(arr.tobytes(order="C"))

            # Range tracking, NaN-safe so profile-restricted fields
            # don't poison the bounds.
            finite = np.isfinite(arr)
            for c in range(spec.n_components):
                col = arr[:, c][finite[:, c]]
                if col.size:
                    comp_min[c] = min(comp_min[c], float(col.min()))
                    comp_max[c] = max(comp_max[c], float(col.max()))

            if spec.n_components >= 3:
                mag = np.linalg.norm(arr[:, :3], axis=1)
                mag = mag[np.isfinite(mag)]
                if mag.size:
                    mag_min = min(mag_min, float(mag.min()))
                    mag_max = max(mag_max, float(mag.max()))
            seen += 1

    if seen != spec.n_steps:
        raise ValueError(f"Field {spec.name!r} streamed {seen} steps but spec says {spec.n_steps}.")

    range_per_comp: dict[str, tuple[float, float]] = {}
    for c, name in enumerate(spec.components):
        if np.isfinite(comp_min[c]) and np.isfinite(comp_max[c]):
            range_per_comp[name] = (float(comp_min[c]), float(comp_max[c]))
        else:
            # All-NaN field — fall back to (0, 0) so the manifest
            # stays JSON-encodable.
            range_per_comp[name] = (0.0, 0.0)

    if not (np.isfinite(mag_min) and np.isfinite(mag_max)):
        mag_min, mag_max = 0.0, 0.0

    return FieldArtefactMeta(
        spec=spec,
        blob_filename=out_path.name,
        stride_bytes=stride,
        scalar_range_per_component=range_per_comp,
        scalar_range_magnitude=(float(mag_min), float(mag_max)),
    )


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


def _encode_elem_field_blob_header(spec: ElementFieldSpec, stride_bytes: int) -> bytes:
    """Binary header for the AFEL blob — same 12-byte (magic + version
    + json_len) prefix shape as AFBL, zero-padded to 1 KB. JSON
    payload carries only O(1) shape metadata; ``element_labels`` and
    ``ip_layout`` live in the manifest so the binary header stays
    well below the 1 KB budget even for very large element counts."""

    header_obj = {
        "name": spec.name,
        "elem_type": spec.elem_type,
        "n_steps": spec.n_steps,
        "n_elements": spec.n_elements,
        "n_ips": spec.n_ips,
        "n_components": spec.n_components,
        "dtype": spec.dtype.name,
        "stride_bytes": stride_bytes,
    }
    json_bytes = json.dumps(header_obj, separators=(",", ":")).encode("utf-8")
    if 12 + len(json_bytes) > ELEM_FIELD_HEADER_BYTES:
        raise ValueError(
            f"AFEL header for {spec.name!r}/{spec.elem_type} doesn't fit in "
            f"{ELEM_FIELD_HEADER_BYTES} bytes (needs {12 + len(json_bytes)})."
        )
    prefix = ELEM_FIELD_MAGIC + struct.pack("<II", ELEM_FIELD_VERSION, len(json_bytes)) + json_bytes
    return prefix + b"\x00" * (ELEM_FIELD_HEADER_BYTES - len(prefix))


def write_element_field_blob_streaming(
    reader: FEAStreamReader,
    spec: ElementFieldSpec,
    out_path: os.PathLike,
) -> ElementFieldArtefactMeta:
    """Stream one (field, elem_type) bucket's step-stack to disk.

    Per-step payload shape is ``(n_elements, n_ips, n_components)``
    float32. Scalar ranges (per-component + magnitude over the first
    3 components when the field has at least 3) are computed inline
    so the manifest can pin the colour LUT across all steps.
    """

    out_path = pathlib.Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    n_components = spec.n_components
    stride = spec.n_elements * spec.n_ips * n_components * spec.dtype.itemsize

    comp_min = np.full(n_components, np.inf, dtype=np.float64)
    comp_max = np.full(n_components, -np.inf, dtype=np.float64)
    mag_min = np.inf
    mag_max = -np.inf

    with open(out_path, "wb") as f:
        f.write(_encode_elem_field_blob_header(spec, stride))
        seen = 0
        for sv in reader.iter_element_field_steps(spec):
            arr = np.asarray(sv.values, dtype=spec.dtype)
            if arr.shape != (spec.n_elements, spec.n_ips, n_components):
                raise ValueError(
                    f"Element field {spec.name!r}/{spec.elem_type} step "
                    f"{sv.step_index} produced shape {arr.shape}, expected "
                    f"{(spec.n_elements, spec.n_ips, n_components)}."
                )
            f.write(np.ascontiguousarray(arr).tobytes(order="C"))

            finite = np.isfinite(arr)
            for c in range(n_components):
                col = arr[..., c][finite[..., c]]
                if col.size:
                    comp_min[c] = min(comp_min[c], float(col.min()))
                    comp_max[c] = max(comp_max[c], float(col.max()))

            if n_components >= 3:
                # Magnitude over the first 3 components — for stress
                # tensors this isn't the von Mises invariant but still
                # gives a sensible default colour range; the von-Mises
                # reduction is a frontend-side option.
                first3 = arr[..., :3]
                mag = np.linalg.norm(first3, axis=-1)
                mag = mag[np.isfinite(mag)]
                if mag.size:
                    mag_min = min(mag_min, float(mag.min()))
                    mag_max = max(mag_max, float(mag.max()))
            seen += 1

    if seen != spec.n_steps:
        raise ValueError(
            f"Element field {spec.name!r}/{spec.elem_type} streamed {seen} " f"steps but spec says {spec.n_steps}."
        )

    range_per_comp: dict[str, tuple[float, float]] = {}
    for c, name in enumerate(spec.components):
        if np.isfinite(comp_min[c]) and np.isfinite(comp_max[c]):
            range_per_comp[name] = (float(comp_min[c]), float(comp_max[c]))
        else:
            range_per_comp[name] = (0.0, 0.0)
    if not (np.isfinite(mag_min) and np.isfinite(mag_max)):
        mag_min, mag_max = 0.0, 0.0

    return ElementFieldArtefactMeta(
        spec=spec,
        blob_filename=out_path.name,
        stride_bytes=stride,
        scalar_range_per_component=range_per_comp,
        scalar_range_magnitude=(float(mag_min), float(mag_max)),
    )


# ---------------------------------------------------------------------------
# Manifest writer
# ---------------------------------------------------------------------------


def _default_view_for(spec: FieldSpec) -> dict:
    """Pick the picker's initial state for a field. Vector fields default
    to magnitude reduction; scalars default to the field itself. All
    fields use viridis."""

    return {
        "reduction": "magnitude" if spec.n_components >= 3 else "scalar",
        "colormap": "viridis",
    }


def _infer_analysis_kind(spec: FieldSpec) -> str:
    """Infer 'static' vs 'eigen' from a field's step value sequence.

    The bake doesn't carry the original analysis-type flag, but the
    streaming readers populate ``step_values`` with eigen frequencies
    for modal output (monotonically increasing positives) and time
    values for transient/static (typically starts at zero, may be a
    single step). One eigen tell: a typical mode shape produces a
    single field with multiple steps where the first value is
    strictly positive and unique. A static analysis with multiple
    steps starts at t=0. Single-step + zero-time → static.

    Picker drives the deformation-scale slider range from this:
    static = [0, 1] (displacement is one-directional, signed sweep
    isn't physical), eigen = [-1, +1] (mode shape has no inherent
    sign).
    """

    if spec.n_steps == 0:
        return "static"
    first = float(spec.step_values[0])
    # Eigen analyses produce strictly positive frequencies starting
    # from a non-zero value; static/transient runs almost always
    # start at t=0.
    if first > 0.0 and spec.n_steps >= 1:
        # Single-step at non-zero might still be static at a finite
        # time, but the conservative call is "treat as eigen" only
        # when we have a clear modal signature: multi-step ascending
        # positives.
        if spec.n_steps >= 2:
            ascending = all(spec.step_values[i + 1] > spec.step_values[i] for i in range(spec.n_steps - 1))
            if ascending:
                return "eigen"
        else:
            return "eigen"
    return "static"


def build_manifest(
    src: str,
    mesh_geom: MeshGeometry,
    mesh_glb_filename: str,
    field_metas: list[FieldArtefactMeta],
    *,
    elem_field_metas: list[ElementFieldArtefactMeta] | None = None,
    mesh_edges_filename: str | None = None,
    n_edges: int = 0,
    beam_solids_glb_filename: str | None = None,
    beam_solids_elements_filename: str | None = None,
    beam_solids_warp_filename: str | None = None,
    beam_solids_edges_filename: str | None = None,
    n_beam_solid_edges: int = 0,
    n_beam_solids: int = 0,
    n_beam_solid_verts: int = 0,
    n_beam_total: int = 0,
    beam_solids_skip_reasons: dict | None = None,
    mesh_elements_filename: str | None = None,
    n_elements: int = 0,
    history: "HistoryRecords | None" = None,
    lineage: dict | None = None,
    fem_concepts: dict | None = None,
    groups: list[dict] | None = None,
    legacy_glb_url_template: str | None = None,
) -> dict:
    """Compose the manifest dict from the bake outputs.

    Element-field metas are grouped by ``spec.name`` so a single
    logical field (e.g. ``STRESS``) carries multiple ``per_type``
    buckets — one per element type the source ships with.

    ``lineage`` (optional) carries the CAD↔FEA back-reference that
    adapy's writers stamp into format-specific sidecars (currently
    the code_aster ``<name>.adapy_fem.json``). Shape:
    ``{"assembly_guid": str, "groups": [{"parent_object_guid": str,
    "parent_object_name": str, "members": ["E17", ...]}]}``.
    Frontend feeds this to ``useLineageStore`` so a click in the FEA
    viewer can jump to the parent beam in a loaded CAD overlay.

    ``fem_concepts`` (optional) carries the FEA *input* concepts —
    point masses, boundary conditions, and per-case / combination load
    scenarios — read back from the same deck-write sidecar (the .rmed
    result has none of them). Same shape as the ``fem_concepts``
    glTF-extension block; the frontend renders it via the shared
    FemConceptsController overlay in the viewer's FEM mode."""

    n_cells = sum(int(cb.data.shape[0]) for cb in mesh_geom.cell_blocks)
    fields_payload = []
    for fm in field_metas:
        spec = fm.spec
        scalar_range = {**fm.scalar_range_per_component}
        if spec.n_components >= 3:
            scalar_range["magnitude"] = list(fm.scalar_range_magnitude)
        # Convert tuple → list for JSON-friendly shape.
        scalar_range = {k: list(v) for k, v in scalar_range.items()}

        steps = [
            {"i": i, "value": float(v), "label": _format_step_label(spec, i, v)} for i, v in enumerate(spec.step_values)
        ]

        fields_payload.append(
            {
                "name_canonical": spec.name,
                "name_native": spec.name,
                "kind": spec.kind,
                "category": spec.category,
                "support": spec.support,
                "analysis_kind": _infer_analysis_kind(spec),
                "components": spec.components,
                "blob": {
                    "url": fm.blob_filename,
                    "header_bytes": BLOB_HEADER_BYTES,
                    "stride_bytes": fm.stride_bytes,
                    "dtype": spec.dtype.name,
                    "byte_order": "little",
                },
                "n_steps": spec.n_steps,
                "steps": steps,
                "scalar_range": scalar_range,
                "default_view": _default_view_for(spec),
            }
        )

    # Element fields. Group by field name so STRESS on QUAD + TRI lands
    # under one manifest entry with two per_type buckets. Within a
    # logical field the bake assumes step counts + canonical step
    # values match across element types (Sesam emits parallel step
    # sets for all element types) — surface a hard error otherwise so
    # the frontend doesn't silently de-sync per-type animation.
    from collections import defaultdict

    elem_by_name: dict[str, list[ElementFieldArtefactMeta]] = defaultdict(list)
    for em in elem_field_metas or []:
        elem_by_name[em.spec.name].append(em)
    for name, metas in elem_by_name.items():
        primary = metas[0].spec
        for em in metas[1:]:
            if em.spec.step_values != primary.step_values:
                raise ValueError(
                    f"Element field {name!r}: step_values differ between "
                    f"types ({primary.elem_type} vs {em.spec.elem_type}); "
                    f"the bake currently requires aligned step sets."
                )
            if em.spec.components != primary.components:
                raise ValueError(
                    f"Element field {name!r}: components differ between "
                    f"types ({primary.elem_type} vs {em.spec.elem_type})."
                )

        # Roll up per-component range across all per_type buckets so
        # the colour LUT stays fixed when the user switches IP / layer
        # / reduction without re-fetching.
        roll_comp: dict[str, tuple[float, float]] = {}
        roll_mag = (float("inf"), float("-inf"))
        for em in metas:
            for cname, (lo, hi) in em.scalar_range_per_component.items():
                if cname in roll_comp:
                    rlo, rhi = roll_comp[cname]
                    roll_comp[cname] = (min(rlo, lo), max(rhi, hi))
                else:
                    roll_comp[cname] = (lo, hi)
            mlo, mhi = em.scalar_range_magnitude
            roll_mag = (min(roll_mag[0], mlo), max(roll_mag[1], mhi))
        if not (roll_mag[0] != float("inf") and roll_mag[1] != float("-inf")):
            roll_mag = (0.0, 0.0)

        scalar_range_payload = {k: list(v) for k, v in roll_comp.items()}
        if primary.n_components >= 3:
            scalar_range_payload["magnitude"] = list(roll_mag)

        steps = [
            {"i": i, "value": float(v), "label": _format_step_label_simple(primary.n_steps, primary.name, v)}
            for i, v in enumerate(primary.step_values)
        ]

        per_type = []
        for em in metas:
            es = em.spec
            per_type.append(
                {
                    "elem_type": es.elem_type,
                    "n_elements": es.n_elements,
                    "n_ips": es.n_ips,
                    "ip_layout": es.ip_layout,
                    "element_labels": es.element_labels,
                    "blob": {
                        "url": em.blob_filename,
                        "header_bytes": ELEM_FIELD_HEADER_BYTES,
                        "stride_bytes": em.stride_bytes,
                        "dtype": es.dtype.name,
                        "byte_order": "little",
                    },
                    "scalar_range": {k: list(v) for k, v in em.scalar_range_per_component.items()},
                }
            )

        # Synthesise a kind so the frontend's existing
        # ``kind.startsWith("vector")`` checks treat 3-component
        # element fields the same as 3-component nodal vectors. Element
        # fields don't have a "displacement / mode shape" axis, so the
        # analysis_kind tracker just falls back to "static".
        n_comp = len(primary.components)
        if n_comp == 1:
            kind = "scalar"
        elif n_comp == 3:
            kind = "vector3"
        elif n_comp == 6:
            kind = "tensor6"
        else:
            kind = f"vector{n_comp}"

        fields_payload.append(
            {
                "name_canonical": primary.name,
                "name_native": primary.name,
                "kind": kind,
                "category": primary.category,
                "support": primary.support,
                "analysis_kind": "static",
                "components": primary.components,
                "n_steps": primary.n_steps,
                "steps": steps,
                "scalar_range": scalar_range_payload,
                "default_view": {
                    "reduction": "magnitude" if n_comp >= 3 else "scalar",
                    "colormap": "viridis",
                    # Default layer/IP for element fields — the frontend's
                    # picker uses these as the initial dropdown values.
                    "layer": "top",
                    "ip_reduction": "max_abs",
                },
                # ``per_type`` distinguishes element fields from nodal in
                # the manifest — when present, the frontend takes the
                # AFEL render path; when absent, the legacy nodal path.
                "per_type": per_type,
            }
        )

    mesh_meta: dict = {
        "url": mesh_glb_filename,
        "n_points": int(mesh_geom.points.shape[0]),
        "n_cells": n_cells,
    }
    if mesh_edges_filename is not None:
        mesh_meta["edges_url"] = mesh_edges_filename
        mesh_meta["n_edges"] = int(n_edges)
    if mesh_elements_filename is not None:
        mesh_meta["elements_url"] = mesh_elements_filename
        mesh_meta["n_elements"] = int(n_elements)
    if beam_solids_glb_filename is not None:
        # Parallel beam-solid mesh emitted when the reader carried
        # section + axis info per beam (SIF today). Frontend renders
        # it alongside the main mesh and can toggle between line and
        # solid display. Per-element draw ranges are keyed by the
        # line-element label so AFEL element-field colours follow.
        mesh_meta["beam_solids_url"] = beam_solids_glb_filename
        if beam_solids_elements_filename is not None:
            mesh_meta["beam_solids_elements_url"] = beam_solids_elements_filename
        if beam_solids_warp_filename is not None:
            # AFBV — per-vertex (node0_idx, node1_idx, t) mapping that
            # lets the frontend lerp nodal displacements onto the
            # solid mesh's vertices so the solid beam stays connected
            # to the rest of the structure under any morph scale.
            mesh_meta["beam_solids_warp_url"] = beam_solids_warp_filename
            mesh_meta["n_beam_solid_verts"] = int(n_beam_solid_verts)
        if beam_solids_edges_filename is not None:
            # AFEG element-boundary wireframe over the solid mesh.
            # Frontend renders this as a LineSegments sharing the
            # beam-solid's position + morph attributes so the seams
            # between adjacent beam-elements stay visible even under
            # a scaled deformation.
            mesh_meta["beam_solids_edges_url"] = beam_solids_edges_filename
            mesh_meta["n_beam_solid_edges"] = int(n_beam_solid_edges)
        mesh_meta["n_beam_solids"] = int(n_beam_solids)
        # Coverage telemetry: total source-side beams + skip reasons
        # by category. Frontend can render "X of Y beams shown as
        # solids" with a tooltip listing the skipped categories so
        # users know what's missing without parsing logs.
        if n_beam_total:
            mesh_meta["n_beam_total"] = int(n_beam_total)
        if beam_solids_skip_reasons:
            mesh_meta["beam_solids_skip_reasons"] = {str(k): int(v) for k, v in beam_solids_skip_reasons.items()}

    manifest: dict = {
        "version": MANIFEST_VERSION,
        "src": src,
        "mesh": mesh_meta,
        "fields": fields_payload,
    }
    if history is not None and (history.regions or history.variables or history.series):
        manifest["history"] = build_history_payload(history)
    if lineage is not None and (lineage.get("assembly_guid") or lineage.get("groups")):
        manifest["lineage"] = lineage
    # FEA input concepts (masses / BCs / load scenarios), carried from
    # adapy's deck-write sidecar. Same shape as the ``fem_concepts``
    # glTF-extension block so the frontend renders it via the shared
    # FemConceptsController overlay.
    if fem_concepts:
        manifest["fem_concepts"] = fem_concepts
    # FEM node/element sets, for the Scene > FEM groups picker (the streaming mesh.glb carries
    # no ADA_EXT, so the frontend feeds these into useSceneInfoStore directly).
    if groups:
        manifest["groups"] = groups
    if legacy_glb_url_template is not None:
        manifest["legacy_glb"] = {"url_template": legacy_glb_url_template}
    return manifest


def build_history_payload(history: "HistoryRecords") -> dict:
    """Serialise a :class:`HistoryRecords` to the manifest's ``history``
    section. Plain dict / list / float — no numpy types — so
    ``json.dumps`` accepts it without a custom encoder."""

    return {
        "regions": [
            {
                "id": r.id,
                "kind": r.kind,
                "instance": r.instance,
                "label": r.label,
                "display_name": r.display_name or r.label,
                **({"coords": list(r.coords)} if r.coords is not None else {}),
            }
            for r in history.regions
        ],
        "variables": [
            {
                "name_native": v.name_native,
                "name_canonical": v.name_canonical or v.name_native,
                "category": v.category,
                "component": v.component,
                "group": v.group,
                "unit": v.unit,
            }
            for v in history.variables
        ],
        "steps": [
            {
                "i": s.i,
                "name": s.name,
                "procedure": s.procedure,
                "domain": s.domain,
            }
            for s in history.steps
        ],
        "series": [
            {
                "region_id": s.region_id,
                "variable": s.variable,
                "step_idx": s.step_idx,
                "times": [float(t) for t in s.times],
                "values": [float(v) for v in s.values],
            }
            for s in history.series
        ],
    }


def _format_step_label(spec: FieldSpec, i: int, v: float) -> str:
    """Picker-display label per step. Single-step fields show the field
    name; multi-step fields show the step value with `:g` formatting,
    matching meshio's convention so existing fixtures keep their look."""

    return _format_step_label_simple(spec.n_steps, spec.name, v)


def _format_step_label_simple(n_steps: int, name: str, v: float) -> str:
    """FieldSpec-free variant for the element-field manifest path —
    those fields use :class:`ElementFieldSpec` which doesn't share a
    base class with :class:`FieldSpec`. Same label semantics."""

    if n_steps == 1:
        return name
    return f"{v:g}"


def write_manifest(manifest: dict, out_path: os.PathLike) -> None:
    out_path = pathlib.Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)


# ---------------------------------------------------------------------------
# Bake orchestrator
# ---------------------------------------------------------------------------


_StreamReaderFactory = Callable[[pathlib.Path], "FEAStreamReader"]
_STREAM_READERS: dict[str, _StreamReaderFactory] = {}


def register_stream_reader(suffix: str, factory: _StreamReaderFactory) -> None:
    """Register a streaming-reader factory for files ending in ``suffix``.

    ``factory(path)`` must return an object satisfying ``FEAStreamReader``.
    Registrations override built-ins for the same suffix — downstream
    packages (e.g. an Abaqus-aware worker registering ``.odb``) should
    call this at startup before any bake call."""

    _STREAM_READERS[suffix] = factory


def _make_rmed_reader(path: pathlib.Path) -> "FEAStreamReader":
    from ada.fem.formats.code_aster.read.med_stream_reader import RmedStreamReader

    return RmedStreamReader(path)


def _make_sif_reader(path: pathlib.Path) -> "FEAStreamReader":
    from ada.fem.formats.sesam.results.read_sif import read_sif_file

    return FEAResultStreamAdapter(read_sif_file(path))


def _make_sin_reader(path: pathlib.Path) -> "FEAStreamReader":
    # Pure-Python Sesam Norsam-binary reader (see
    # ada.fem.formats.sesam.results.read_sin). No Prepost.exe shell-out
    # and no SIF text intermediate — feeds the streaming bake directly.
    from ada.fem.formats.sesam.results.read_sin import read_sin_file

    return FEAResultStreamAdapter(read_sin_file(path))


def _make_fem_reader(path: pathlib.Path) -> "FEAStreamReader":
    """Stream-reader for a results-less FEM mesh (.inp / .fem).

    A design-model FEM deck is a mesh with no solver results. Reading it through the same
    streaming-artefact pipeline as real results (mesh + edges + beam-solids, just an empty
    fields list) is what unifies FE-mesh visualisation onto one path. Multipart decks are
    merged first; ``FEM.to_mesh()`` supplies the section/material tables the beam-solid
    tessellation needs.
    """
    import ada
    from ada.fem.concat import concatenate_fem_meshes
    from ada.fem.results.common import ElementBlock, FEAResult

    assembly = ada.from_fem(path)
    parts = [
        p
        for p in assembly.get_all_parts_in_assembly(include_self=True)
        if p.fem is not None and len(p.fem.nodes) > 0
    ]
    if not parts:
        raise ValueError(f"no FEM mesh found in {path}")

    # Non-destructive merge: keep each part's FEM in the assembly tree (concepts are scraped
    # from the un-merged assembly below) while producing one merged Mesh for the viewer.
    # FEM.to_mesh() per part supplies the section/material tables the beam-solid path needs.
    mesh, part_offsets = concatenate_fem_meshes(parts)
    # to_elem_blocks() emits row-index node_refs (array-substrate convention); the adapter +
    # geometry/field readers expect node IDs (they remap id->index). Convert once here.
    ids = mesh.nodes.identifiers
    rebuilt: list[ElementBlock] = []
    for b in mesh.elements:
        if b.node_refs_are_indices:
            nref = ids[np.asarray(b.node_refs)]
            rebuilt.append(ElementBlock(b.elem_info, nref, b.identifiers, node_refs_are_indices=False))
        else:
            rebuilt.append(b)
    mesh.elements = rebuilt
    result = FEAResult(name=parts[0].fem.name or path.stem, software="adapy", results=[], mesh=mesh)
    reader = FEAResultStreamAdapter(result)
    # Scrape FEA input concepts (masses / BCs / load scenarios) so the Scene > FEM panel can
    # draw the glyph overlay. Built from the (intact) assembly — positions are coordinates.
    try:
        from ada.extension.fem_concepts_builder import build_combined_fem_concepts

        fc = build_combined_fem_concepts(assembly)
        if fc is not None:
            reader._fem_concepts = fc.model_dump(mode="json", exclude_none=True)
    except Exception as e:  # noqa: BLE001 — concepts are best-effort decoration
        from ada.config import get_logger

        get_logger().debug("FEM bake: fem_concepts scrape failed: %s", e)

    # Scrape each part's node/element sets into manifest groups for the Scene > FEM groups picker
    # (the streaming mesh.glb carries no ADA_EXT). Member ids carry the same per-part offset as
    # the merged mesh so EL{id}/P{id} resolve against the AFEM element ranges. Multi-part set
    # names are prefixed with the part name to disambiguate.
    try:
        groups: list[dict] = []
        multipart = len(parts) > 1
        for p, (nid_off, elid_off) in zip(parts, part_offsets):
            for fset in p.fem.sets:
                is_nset = fset.type == fset.TYPES.NSET
                off = nid_off if is_nset else elid_off
                prefix = "P" if is_nset else "EL"
                mids = getattr(fset, "_member_ids", None)
                if mids is None:
                    mids = [m.id for m in fset.members if getattr(m, "id", None) is not None]
                members = [f"{prefix}{int(i) + off}" for i in mids]
                if members:
                    name = f"{p.name}_{fset.name}" if multipart else fset.name
                    groups.append(
                        {"name": name, "members": members, "fe_object_type": "node" if is_nset else "element"}
                    )
        reader._groups = groups or None
    except Exception as e:  # noqa: BLE001 — groups are best-effort decoration
        from ada.config import get_logger

        get_logger().debug("FEM bake: groups scrape failed: %s", e)
    return reader


def _ensure_builtin_stream_readers() -> None:
    if getattr(_ensure_builtin_stream_readers, "_done", False):
        return
    # setdefault: a downstream registration for the same suffix wins.
    _STREAM_READERS.setdefault(".rmed", _make_rmed_reader)
    _STREAM_READERS.setdefault(".sif", _make_sif_reader)
    _STREAM_READERS.setdefault(".sin", _make_sin_reader)
    # Design-model FEM meshes flow through the same streaming bake (mesh + beam-solids, no
    # result fields) so FE-mesh visualisation has a single path. (.rmed keeps its native
    # results streamer above; plain .med is a mesh-only deck read via from_fem.)
    _STREAM_READERS.setdefault(".inp", _make_fem_reader)
    _STREAM_READERS.setdefault(".fem", _make_fem_reader)
    _STREAM_READERS.setdefault(".med", _make_fem_reader)
    _ensure_builtin_stream_readers._done = True  # type: ignore[attr-defined]


def fea_artefact_extensions() -> frozenset[str]:
    """Set of source-file suffixes the streaming bake can open."""

    _ensure_builtin_stream_readers()
    return frozenset(_STREAM_READERS)


def is_fea_artefact_source(src_key_or_path) -> bool:
    """True if the source extension is in scope for the streaming bake."""

    suffix = pathlib.PurePosixPath(str(src_key_or_path)).suffix.lower()
    return suffix in fea_artefact_extensions()


def make_stream_reader(src_path: os.PathLike) -> FEAStreamReader:
    """Open the right streaming reader for a source file's extension.

    Dispatch goes through ``_STREAM_READERS``; built-ins (``.rmed`` /
    ``.sif``) self-register on first call. Caller is responsible for
    closing the returned reader (use as a context manager)."""

    _ensure_builtin_stream_readers()
    src_path = pathlib.Path(src_path)
    ext = src_path.suffix.lower()
    factory = _STREAM_READERS.get(ext)
    if factory is None:
        raise ValueError(
            f"no streaming reader for FEA source extension {ext!r}; " f"registered: {sorted(_STREAM_READERS)}"
        )
    return factory(src_path)


def bake_fea_artefacts_from_source(
    src_path: os.PathLike,
    out_dir: os.PathLike,
    *,
    src_key: str = "",
    legacy_glb_url_template: str | None = None,
) -> "BakeResult":
    """End-to-end bake from a source file path. Picks the right
    reader for the extension and drives the streaming bake. Raises
    ``ValueError`` for unsupported extensions; the caller (REST
    endpoint, CLI, tests) is responsible for the policy decision of
    when to surface that vs route to a different code path."""

    src_path = pathlib.Path(src_path)
    src = src_key or src_path.stem
    with make_stream_reader(src_path) as reader:
        return bake_artefacts(
            reader,
            out_dir,
            src=src,
            legacy_glb_url_template=legacy_glb_url_template,
        )


@dataclass
class BakeResult:
    out_dir: pathlib.Path
    manifest_path: pathlib.Path
    mesh_glb_path: pathlib.Path
    field_blob_paths: list[pathlib.Path] = dc_field(default_factory=list)


def bake_artefacts(
    reader: FEAStreamReader,
    out_dir: os.PathLike,
    *,
    src: str = "",
    legacy_glb_url_template: str | None = None,
    nodal_only: bool = True,
    include_element_fields: bool = True,
) -> BakeResult:
    """Drive the streaming bake end-to-end.

    Nodal fields produce one AFBL blob each. Element fields (gauss /
    element_nodal) produce one AFEL blob per (field, element-type);
    these are grouped under one manifest record per logical field
    with ``per_type`` buckets. Set ``include_element_fields=False``
    to skip the element-field emission entirely — useful for tests
    that only exercise the nodal path.

    ``nodal_only`` is kept as a backwards-compat alias: when True,
    ``iter_field_steps`` callers still drop non-nodal specs (the
    nodal blob writer can't handle them). Element fields flow
    through the new ``iter_element_field_steps`` path instead.
    """

    out_dir = pathlib.Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    geom = reader.read_mesh_geometry()

    # One topology walk feeds three writers (GLB faces, edge sidecar,
    # element sidecar). Each writer used to walk per-element shapes
    # independently — for a 100k-element mesh the savings from a
    # single pass are non-trivial and the AFEM ranges have to come
    # out of the same iteration order as the GLB faces or selection
    # would target the wrong triangles.
    topology = _compute_topology(geom)

    mesh_glb_path = out_dir / "fea.mesh.glb"
    write_mesh_glb(geom, mesh_glb_path, faces=topology.faces)

    # Element edges (deduped) — frontend renders them as a
    # LineSegments overlay sharing the mesh's position attribute,
    # so the wireframe shows actual element boundaries (not the
    # arbitrary diagonals from quad-face triangulation) and follows
    # the deformation automatically.
    mesh_edges_path = out_dir / "fea.mesh.edges.bin"
    n_edges = write_mesh_edges(geom, mesh_edges_path, edges=topology.edges)

    # Per-element draw ranges — frontend hydrates these into
    # userdata.id_hierarchy + userdata.draw_ranges_<meshName> so the
    # FEA mesh enters the existing CustomBatchedMesh pick + highlight
    # pipeline without a parallel selection path.
    mesh_elements_path = out_dir / "fea.mesh.elements.bin"
    n_elements = write_mesh_elements(geom, mesh_elements_path, element_ranges=topology.element_ranges)

    # Beam-solid mesh — optional, depends on whether the reader has
    # section + axis info per beam (SIF via FEAResultStreamAdapter
    # today). Skipped silently when the reader returns None: the
    # frontend reads the manifest and falls back to line-only beam
    # rendering when ``beam_solids_url`` is absent.
    beam_solids_glb_path: pathlib.Path | None = None
    beam_solids_elements_path: pathlib.Path | None = None
    n_beam_solids = 0
    try:
        solid_beams = reader.try_solid_beams()
    except (AttributeError, NotImplementedError):
        solid_beams = None
    beam_solids_warp_path: pathlib.Path | None = None
    beam_solids_edges_path: pathlib.Path | None = None
    n_beam_solid_verts = 0
    n_beam_solid_edges = 0
    if solid_beams is not None and solid_beams.triangles.size:
        beam_solids_glb_path = out_dir / "fea.beam_solids.glb"
        write_beam_solids_glb(solid_beams, beam_solids_glb_path)
        beam_solids_elements_path = out_dir / "fea.beam_solids.elements.bin"
        n_beam_solids = write_beam_solids_elements(solid_beams, beam_solids_elements_path)
        # AFBV warp mapping — every solid vertex's parent beam
        # endpoints + axial parameter. Skip when the reader didn't
        # populate the vertex_* arrays (defensive: the SIF adapter
        # always does, but a future reader might omit it).
        if solid_beams.vertex_node0.size:
            beam_solids_warp_path = out_dir / "fea.beam_solids.warp.bin"
            n_beam_solid_verts = write_beam_solids_warp(solid_beams, beam_solids_warp_path)
        # AFEG element-boundary wireframe for the solid mesh. Without
        # this the beam solids render as one continuous tube — see the
        # writer docstring for the boundary-edge rules.
        beam_solids_edges_path = out_dir / "fea.beam_solids.edges.bin"
        n_beam_solid_edges = write_beam_solids_edges(solid_beams, beam_solids_edges_path)

    field_metas: list[FieldArtefactMeta] = []
    blob_paths: list[pathlib.Path] = []
    for spec in reader.field_specs():
        if nodal_only and spec.support != "nodal":
            continue
        blob_path = out_dir / f"fea.{spec.name}.bin"
        meta = write_field_blob_streaming(reader, spec, blob_path)
        field_metas.append(meta)
        blob_paths.append(blob_path)

    elem_field_metas: list[ElementFieldArtefactMeta] = []
    if include_element_fields:
        # Best-effort: a reader that hasn't implemented the
        # element-field protocol yet (returns from a Protocol stub or
        # raises NotImplementedError) just contributes no element
        # buckets. Surface AttributeError as the explicit signal so
        # other failures still bubble up.
        try:
            elem_specs = reader.element_field_specs()
        except (AttributeError, NotImplementedError):
            elem_specs = []
        for es in elem_specs:
            # Filename includes elem_type so each (field, type) bucket
            # gets a distinct file the frontend can range-fetch.
            blob_path = out_dir / f"fea.{es.name}.{es.elem_type}.elements.bin"
            em = write_element_field_blob_streaming(reader, es, blob_path)
            elem_field_metas.append(em)
            blob_paths.append(blob_path)

    # History output — time series at monitored points. Optional; the
    # bake tolerates readers that pre-date the method (AttributeError)
    # and readers that simply have no history data for this source
    # (None return).
    try:
        history = reader.try_history_records()
    except (AttributeError, NotImplementedError):
        history = None

    # CAD↔FEA lineage. Pulled from a format-specific sidecar (e.g.
    # ``<name>.adapy_fem.json`` for code_aster) that adapy's FEM
    # writer stamps at deck-write time. Readers that don't implement
    # the method, or sources without an adapy-written sidecar, just
    # produce no lineage and the manifest entry is omitted.
    try:
        lineage = reader.try_lineage()
    except (AttributeError, NotImplementedError):
        lineage = None

    # FEA input concepts (masses / BCs / load scenarios). Same sidecar
    # source as lineage — present only when adapy wrote the deck (the
    # .rmed itself has no inputs). Readers that pre-date the method, or
    # sources without a v5 sidecar, contribute nothing and the manifest
    # key is omitted.
    try:
        fem_concepts = reader.try_fem_concepts()
    except (AttributeError, NotImplementedError):
        fem_concepts = None

    # FEM node/element sets -> manifest groups (Scene > FEM groups picker). Readers without the
    # method (SIF/SIN/RMED) contribute nothing.
    try:
        groups = reader.try_groups()
    except (AttributeError, NotImplementedError):
        groups = None

    manifest = build_manifest(
        src=src,
        mesh_geom=geom,
        mesh_glb_filename=mesh_glb_path.name,
        field_metas=field_metas,
        elem_field_metas=elem_field_metas,
        mesh_edges_filename=mesh_edges_path.name,
        n_edges=n_edges,
        mesh_elements_filename=mesh_elements_path.name,
        n_elements=n_elements,
        history=history,
        beam_solids_glb_filename=(beam_solids_glb_path.name if beam_solids_glb_path else None),
        beam_solids_elements_filename=(beam_solids_elements_path.name if beam_solids_elements_path else None),
        beam_solids_warp_filename=(beam_solids_warp_path.name if beam_solids_warp_path else None),
        beam_solids_edges_filename=(beam_solids_edges_path.name if beam_solids_edges_path else None),
        n_beam_solid_edges=n_beam_solid_edges,
        n_beam_solids=n_beam_solids,
        n_beam_solid_verts=n_beam_solid_verts,
        n_beam_total=(solid_beams.total_beams if solid_beams is not None else 0),
        beam_solids_skip_reasons=(solid_beams.skip_reasons if solid_beams is not None else None),
        lineage=lineage,
        fem_concepts=fem_concepts,
        groups=groups,
        legacy_glb_url_template=legacy_glb_url_template,
    )
    manifest_path = out_dir / "fea.manifest.json"
    write_manifest(manifest, manifest_path)

    return BakeResult(
        out_dir=out_dir,
        manifest_path=manifest_path,
        mesh_glb_path=mesh_glb_path,
        field_blob_paths=blob_paths,
    )


# ---------------------------------------------------------------------------
# Bake + static posters
# ---------------------------------------------------------------------------
#
# Static counterpart figures are mandatory in this codebase: the
# downstream consumer (paradoc) exports to PDF / DOCX / ODT, none of
# which can run the interactive deformation-scale slider. Every FEA
# figure that ships through the docs pipeline therefore needs a PNG
# poster per mode it could ever surface interactively. `bake_with_posters`
# wraps `bake_artefacts` + the existing `render_fea_mode_from_bundle`
# pass so callers get the bundle and the per-mode PNG set in one shot,
# instead of hand-rolling the mode-enumeration + filename-convention
# loop in every report script.


@dataclass
class BakeWithPostersResult(BakeResult):
    """Extends :class:`BakeResult` with per-mode static PNG posters.

    Convention (matches the verification report's existing layout so
    the paradoc exporter's ``<glb>.png`` sibling lookup keeps working):

      * Mode 1 → ``<out_dir>/fea.mesh.png`` (also recorded on
        ``canonical_poster_path`` so callers don't have to reconstruct
        the filename).
      * Mode N (N ≥ 2) → ``<out_dir>/fea.mesh.mode_<N>.png``.

    ``poster_paths`` is keyed by the 0-based global mode index — same
    indexing the embed's ``mountFeaArtefactViewer(modeIndex=…)`` uses,
    so a downstream loop ``for i, png in poster_paths.items(): …`` lines
    up directly with mode-view rows that pin ``modeIndex=i``.
    """

    poster_paths: dict[int, pathlib.Path] = dc_field(default_factory=dict)
    canonical_poster_path: pathlib.Path | None = None


def _resolve_mode_selection(
    modes: "str | int | Iterable[int] | None",
    n_available: int,
) -> list[int]:
    """Turn the ``modes`` knob into a concrete list of 0-based indices.

    ``"all"`` → every mode the bundle has.
    ``int N`` → modes 0..min(N, n_available)-1 (i.e. the first N).
    Iterable  → take each value that falls inside ``[0, n_available)``.
    ``None``  → ``[]`` (caller should skip rendering entirely; kept here
    for symmetry so ``modes=None`` in tests behaves predictably).
    """
    if modes is None or n_available <= 0:
        return []
    if modes == "all":
        return list(range(n_available))
    if isinstance(modes, int):
        if modes < 0:
            return []
        return list(range(min(modes, n_available)))
    return [i for i in modes if 0 <= i < n_available]


def bake_with_posters(
    reader_or_result,
    out_dir: os.PathLike,
    *,
    src: str = "",
    modes: "str | int | Iterable[int] | None" = "all",
    poster_backend: Literal["pygfx", "chromium"] = "pygfx",
    legacy_glb_url_template: str | None = None,
    nodal_only: bool = True,
    include_element_fields: bool = True,
) -> BakeWithPostersResult:
    """Bake the artefact bundle AND render per-mode static PNG posters.

    Accepts either a :class:`FEAStreamReader` (the streaming bake's
    native input) or an in-memory :class:`FEAResult` (auto-wrapped via
    :class:`FEAResultStreamAdapter`) — the same ergonomic split as
    ``bake_fea_artefacts_from_source``'s reader-discovery, but driven
    by the object the caller already holds.

    ``modes``:
        * ``"all"`` (default) — one PNG per displacement-field step.
          The docs pipeline needs every mode it might surface
          interactively, so this is the right default for the
          PDF/DOCX/ODT-aware use case.
        * ``int N`` — render the first N modes only.
        * iterable of int — render those specific 0-based modes.
        * ``None`` — bundle only, no posters. Use when the caller is
          handling poster rendering itself (or the figure is
          interactive-only in a context where that's acceptable).

    ``poster_backend``:
        * ``"pygfx"`` (default) — fast, no browser. Camera framing is
          the same `iso_3` math the embed uses.
        * ``"chromium"`` — drives the production embed headless via
          playwright. Bit-identical to the live viewer, ~5 s/PNG.

    Per-mode render failures are logged but don't abort the bake —
    the bundle is still valid even if a few posters are missing, and
    surfacing them as warnings keeps a single bad mode from blowing
    up the whole docs build. Modes that render successfully appear in
    the returned ``poster_paths``; missing modes mean either the
    selection excluded them or that mode's render raised.
    """

    # Auto-wrap FEAResult. We check for the FEAStreamReader Protocol's
    # core method rather than `isinstance(FEAResult)` so future result
    # types (in-progress migration) don't need wiring here — anything
    # that quacks like a reader passes through, everything else gets
    # the FEAResult adapter treatment.
    if hasattr(reader_or_result, "read_mesh_geometry"):
        reader = reader_or_result
    else:
        reader = FEAResultStreamAdapter(reader_or_result)

    bake = bake_artefacts(
        reader,
        out_dir,
        src=src,
        legacy_glb_url_template=legacy_glb_url_template,
        nodal_only=nodal_only,
        include_element_fields=include_element_fields,
    )

    if modes is None:
        return BakeWithPostersResult(
            out_dir=bake.out_dir,
            manifest_path=bake.manifest_path,
            mesh_glb_path=bake.mesh_glb_path,
            field_blob_paths=bake.field_blob_paths,
        )

    # Lazy import — the renderer pulls trimesh + pygfx + numpy heavy
    # machinery, which a slimmer caller (a smoke test that just wants
    # the bundle, say) shouldn't pay for.
    from ada.visit.rendering.fea_offscreen import (
        _list_displacement_entries,
        render_fea_mode_from_bundle,
    )

    manifest = json.loads(bake.manifest_path.read_text(encoding="utf-8"))
    available = _list_displacement_entries(manifest)
    wanted = _resolve_mode_selection(modes, len(available))

    poster_paths: dict[int, pathlib.Path] = {}
    canonical: pathlib.Path | None = None
    for mode_idx in wanted:
        mode_n = mode_idx + 1
        # Mode 1 lands at the `<glb>.png` sibling slot the paradoc
        # exporter (and the cad_model_file figure-source) already looks
        # up for canonical-row posters. Modes ≥ 2 use a per-mode
        # filename so multiple rows referencing the same bundle don't
        # collide on disk.
        if mode_n == 1:
            dest_png = bake.mesh_glb_path.with_suffix(".png")
            canonical = dest_png
        else:
            dest_png = bake.mesh_glb_path.with_name(f"fea.mesh.mode_{mode_n}.png")
        try:
            img = render_fea_mode_from_bundle(
                bake.out_dir,
                mode_index=mode_idx,
                backend=poster_backend,
            )
            img.save(str(dest_png))
            poster_paths[mode_idx] = dest_png
        except Exception as exc:  # noqa: BLE001 — render failure is non-fatal
            import logging

            logging.getLogger(__name__).warning(
                "bake_with_posters: mode %d poster failed: %s",
                mode_n,
                exc,
                exc_info=True,
            )

    return BakeWithPostersResult(
        out_dir=bake.out_dir,
        manifest_path=bake.manifest_path,
        mesh_glb_path=bake.mesh_glb_path,
        field_blob_paths=bake.field_blob_paths,
        poster_paths=poster_paths,
        canonical_poster_path=canonical,
    )


def bake_with_posters_from_source(
    src_path: os.PathLike,
    out_dir: os.PathLike,
    *,
    src_key: str = "",
    modes: "str | int | Iterable[int] | None" = "all",
    poster_backend: Literal["pygfx", "chromium"] = "pygfx",
    legacy_glb_url_template: str | None = None,
) -> BakeWithPostersResult:
    """End-to-end bake + per-mode posters from a result file path.

    Mirrors ``bake_fea_artefacts_from_source``: pick the right stream
    reader for the extension (``.frd`` / ``.odb`` / ``.rmed`` / ``.sif``
    / ``.sin``), drive the streaming bake, then render the static
    posters for the modes selected. Raises :class:`ValueError` for
    unsupported extensions; same policy as the non-poster variant.
    """

    src_path = pathlib.Path(src_path)
    src = src_key or src_path.stem
    with make_stream_reader(src_path) as reader:
        return bake_with_posters(
            reader,
            out_dir,
            src=src,
            modes=modes,
            poster_backend=poster_backend,
            legacy_glb_url_template=legacy_glb_url_template,
        )


# ---------------------------------------------------------------------------
# Blob reader (verification / tests)
# ---------------------------------------------------------------------------


def read_blob_header(path: os.PathLike) -> dict:
    """Return the JSON header from an AFBL blob. Useful for tests and
    for tools that want to introspect a blob without loading payload."""

    path = pathlib.Path(path)
    with open(path, "rb") as f:
        prefix = f.read(BLOB_HEADER_BYTES)
    if prefix[:4] != BLOB_MAGIC:
        raise ValueError(f"{path}: not an AFBL blob (magic {prefix[:4]!r}).")
    version, json_len = struct.unpack("<II", prefix[4:12])
    if version != BLOB_VERSION:
        raise ValueError(f"{path}: blob version {version}, expected {BLOB_VERSION}.")
    return json.loads(prefix[12 : 12 + json_len].decode("utf-8"))


def read_blob_step(path: os.PathLike, step_index: int) -> np.ndarray:
    """Read one step's payload from an AFBL blob. Used by tests; the
    frontend equivalent is a Range fetch."""

    header = read_blob_header(path)
    if step_index < 0 or step_index >= header["n_steps"]:
        raise IndexError(step_index)
    n_points = header["n_points"]
    n_components = header["n_components"]
    dtype = np.dtype(header["dtype"])
    stride = header["stride_bytes"]
    offset = BLOB_HEADER_BYTES + step_index * stride
    with open(path, "rb") as f:
        f.seek(offset)
        buf = f.read(stride)
    arr = np.frombuffer(buf, dtype=dtype).reshape(n_points, n_components)
    return arr


def read_elem_field_blob_header(path: os.PathLike) -> dict:
    """JSON header from an AFEL element-field blob. Mirrors
    :func:`read_blob_header` for the nodal AFBL format."""

    path = pathlib.Path(path)
    with open(path, "rb") as f:
        prefix = f.read(ELEM_FIELD_HEADER_BYTES)
    if prefix[:4] != ELEM_FIELD_MAGIC:
        raise ValueError(f"{path}: not an AFEL blob (magic {prefix[:4]!r}).")
    version, json_len = struct.unpack("<II", prefix[4:12])
    if version != ELEM_FIELD_VERSION:
        raise ValueError(f"{path}: AFEL version {version}, expected {ELEM_FIELD_VERSION}.")
    return json.loads(prefix[12 : 12 + json_len].decode("utf-8"))


def read_elem_field_blob_step(path: os.PathLike, step_index: int) -> np.ndarray:
    """One step's payload from an AFEL blob, shape
    ``(n_elements, n_ips, n_components)``."""

    header = read_elem_field_blob_header(path)
    if step_index < 0 or step_index >= header["n_steps"]:
        raise IndexError(step_index)
    n_elements = header["n_elements"]
    n_ips = header["n_ips"]
    n_components = header["n_components"]
    dtype = np.dtype(header["dtype"])
    stride = header["stride_bytes"]
    offset = ELEM_FIELD_HEADER_BYTES + step_index * stride
    with open(path, "rb") as f:
        f.seek(offset)
        buf = f.read(stride)
    return np.frombuffer(buf, dtype=dtype).reshape(n_elements, n_ips, n_components)
