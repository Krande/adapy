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
from dataclasses import dataclass, field as dc_field
from typing import Iterator, Literal, Protocol

import numpy as np

from ada.fem.results.common import CellBlockData

# Binary format: 4-byte magic, uint32 version, uint32 json_len, JSON
# header, zero-padded to 1024 bytes, then payload. Header version
# bump signals a breaking layout change (e.g. variable-stride steps).
BLOB_MAGIC = b"AFBL"
BLOB_VERSION = 1
BLOB_HEADER_BYTES = 1024
MANIFEST_VERSION = 1

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
ELEM_ENTRY_BYTES = 12   # uint32 label, uint32 tri_start, uint32 tri_count


@dataclass
class MeshGeometry:
    """Geometry-only mesh. The streaming reader produces one of these
    once per source; downstream the writer turns it into the mesh GLB."""

    points: np.ndarray  # (n_points, 3) float
    cell_blocks: list[CellBlockData]


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


class FEAStreamReader(Protocol):
    """Per-format streaming reader interface.

    The bake calls ``read_mesh_geometry`` once, then for each spec in
    ``field_specs`` calls ``iter_field_steps``. The reader is
    responsible for keeping any underlying file handle open across
    those calls.
    """

    def read_mesh_geometry(self) -> MeshGeometry: ...

    def field_specs(self) -> list[FieldSpec]: ...

    def iter_field_steps(self, field_name: str) -> Iterator[StepValues]: ...

    def close(self) -> None: ...


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

        # Remap real node IDs → 0-based point indices. ElementBlock
        # stores arbitrary-id node references (1-based for RMED,
        # arbitrary for SIF/FRD); the artefact pipeline expects
        # 0-based indices into the points array.
        ids = result.mesh.nodes.identifiers
        self._nmap = {int(x): i for i, x in enumerate(ids)}

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
                    f"Element block of type {cell_type_str!r} references "
                    f"unknown node id {e.args[0]}."
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
            cell_blocks.append(
                CellBlockData(
                    cell_type=cell_type_str, data=data_0, identifiers=identifiers
                )
            )

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
            step_values = [
                float(r.eigen_freq if r.eigen_freq is not None else r.step)
                for r in sorted_results
            ]
            components = list(first.components) or [first.name]

            specs.append(
                FieldSpec(
                    name=name,
                    components=components,
                    n_steps=len(sorted_results),
                    n_points=n_points,
                    support=support,
                    step_values=step_values,
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
                f"not implemented in Phase 1"
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
        face_mesh = trimesh.Trimesh(vertices=geom.points, faces=face_arr)
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
        raise ValueError(
            f"Field {spec.name!r} streamed {seen} steps but spec says {spec.n_steps}."
        )

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
            ascending = all(
                spec.step_values[i + 1] > spec.step_values[i]
                for i in range(spec.n_steps - 1)
            )
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
    mesh_edges_filename: str | None = None,
    n_edges: int = 0,
    mesh_elements_filename: str | None = None,
    n_elements: int = 0,
    legacy_glb_url_template: str | None = None,
) -> dict:
    """Compose the manifest dict from the bake outputs."""

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
            {"i": i, "value": float(v), "label": _format_step_label(spec, i, v)}
            for i, v in enumerate(spec.step_values)
        ]

        fields_payload.append(
            {
                "name_canonical": spec.name,
                "name_native": spec.name,
                "kind": spec.kind,
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

    manifest: dict = {
        "version": MANIFEST_VERSION,
        "src": src,
        "mesh": mesh_meta,
        "fields": fields_payload,
    }
    if legacy_glb_url_template is not None:
        manifest["legacy_glb"] = {"url_template": legacy_glb_url_template}
    return manifest


def _format_step_label(spec: FieldSpec, i: int, v: float) -> str:
    """Picker-display label per step. Single-step fields show the field
    name; multi-step fields show the step value with `:g` formatting,
    matching meshio's convention so existing fixtures keep their look."""

    if spec.n_steps == 1:
        return spec.name
    return f"{v:g}"


def write_manifest(manifest: dict, out_path: os.PathLike) -> None:
    out_path = pathlib.Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)


# ---------------------------------------------------------------------------
# Bake orchestrator
# ---------------------------------------------------------------------------


FEA_ARTEFACT_EXTENSIONS: frozenset[str] = frozenset({".rmed", ".sif"})


def is_fea_artefact_source(src_key_or_path) -> bool:
    """True if the source extension is in scope for the streaming bake.
    Phase 1 covers .rmed (native streaming reader) and .sif (FEAResult
    adapter); .frd is Phase 2."""

    suffix = pathlib.PurePosixPath(str(src_key_or_path)).suffix.lower()
    return suffix in FEA_ARTEFACT_EXTENSIONS


def make_stream_reader(src_path: os.PathLike) -> FEAStreamReader:
    """Open the right streaming reader for a source file's extension.

    Native streaming on RMED (h5py-lazy); SIF flows through the
    FEAResult adapter since the SIF reader is eager today. Caller is
    responsible for closing the returned reader (use as a context
    manager)."""

    src_path = pathlib.Path(src_path)
    ext = src_path.suffix.lower()

    if ext == ".rmed":
        from ada.fem.formats.code_aster.read.med_stream_reader import RmedStreamReader

        return RmedStreamReader(src_path)
    if ext == ".sif":
        from ada.fem.formats.sesam.results.read_sif import read_sif_file

        result = read_sif_file(src_path)
        return FEAResultStreamAdapter(result)
    raise ValueError(
        f"no streaming reader for FEA source extension {ext!r}; "
        f"supported: {sorted(FEA_ARTEFACT_EXTENSIONS)}"
    )


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
) -> BakeResult:
    """Drive the streaming bake end-to-end.

    Phase 1 emits nodal fields only (``nodal_only=True``). Element-nodal
    and Gauss-point fields are skipped — they show up in the manifest
    later, when the viewer learns to render them.
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
    n_elements = write_mesh_elements(
        geom, mesh_elements_path, element_ranges=topology.element_ranges
    )

    field_metas: list[FieldArtefactMeta] = []
    blob_paths: list[pathlib.Path] = []
    for spec in reader.field_specs():
        if nodal_only and spec.support != "nodal":
            continue
        blob_path = out_dir / f"fea.{spec.name}.bin"
        meta = write_field_blob_streaming(reader, spec, blob_path)
        field_metas.append(meta)
        blob_paths.append(blob_path)

    manifest = build_manifest(
        src=src,
        mesh_geom=geom,
        mesh_glb_filename=mesh_glb_path.name,
        field_metas=field_metas,
        mesh_edges_filename=mesh_edges_path.name,
        n_edges=n_edges,
        mesh_elements_filename=mesh_elements_path.name,
        n_elements=n_elements,
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
