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


def write_mesh_glb(geom: MeshGeometry, out_path: os.PathLike) -> None:
    """Write a geometry-only GLB (vertices + face indices, no per-step
    or per-vertex data baked in). The frontend renders edges from the
    face topology via a wireframe pass; the legacy GLB pipeline still
    emits explicit edge geometry, but the streaming path doesn't need
    to."""

    import trimesh
    from trimesh.visual.material import PBRMaterial

    from ada.fem.results.common import MeshData
    from ada.visit.rendering.femviz import get_edges_and_faces_from_mesh_data

    mesh_data = MeshData(points=geom.points, cells=geom.cell_blocks)
    _edges, faces = get_edges_and_faces_from_mesh_data(mesh_data)

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


def build_manifest(
    src: str,
    mesh_geom: MeshGeometry,
    mesh_glb_filename: str,
    field_metas: list[FieldArtefactMeta],
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

    manifest: dict = {
        "version": MANIFEST_VERSION,
        "src": src,
        "mesh": {
            "url": mesh_glb_filename,
            "n_points": int(mesh_geom.points.shape[0]),
            "n_cells": n_cells,
        },
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
    mesh_glb_path = out_dir / "fea.mesh.glb"
    write_mesh_glb(geom, mesh_glb_path)

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
