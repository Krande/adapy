"""Streaming nodal and element field blob writers, plus the blob readers used for verification."""

from __future__ import annotations

import json
import os
import pathlib
import struct
from typing import Callable

import numpy as np

from .formats import (
    BLOB_HEADER_BYTES,
    BLOB_MAGIC,
    BLOB_VERSION,
    ELEM_FIELD_HEADER_BYTES,
    ELEM_FIELD_MAGIC,
    ELEM_FIELD_VERSION,
)
from .protocol import FEAStreamReader
from .specs import (
    ElementFieldArtefactMeta,
    ElementFieldSpec,
    ElementStepValues,
    FieldArtefactMeta,
    FieldSpec,
    StepValues,
)

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


class FieldBlobWriter:
    """One nodal field's AFBL blob, written a step at a time.

    :func:`write_field_blob_streaming` drives it from a reader field by field;
    the bake can instead keep one open per field and feed them all a step at a
    time (see :func:`~.bake.bake_artefacts`), which lets a reader that decodes a
    whole step at once decode it once for every field. Either way the bytes and
    the scalar ranges are the same: they depend on the order of a field's own
    steps, never on how the fields are interleaved.

    ``step_transform(step_index, values)``, when given, rewrites each step
    before it is written and ranged (mode normalization).
    """

    def __init__(
        self,
        spec: FieldSpec,
        out_path: os.PathLike,
        step_transform: Callable[[int, np.ndarray], np.ndarray] | None = None,
    ) -> None:
        self.spec = spec
        self.path = pathlib.Path(out_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._transform = step_transform
        self.stride = spec.n_points * spec.n_components * spec.dtype.itemsize
        self._comp_min = np.full(spec.n_components, np.inf, dtype=np.float64)
        self._comp_max = np.full(spec.n_components, -np.inf, dtype=np.float64)
        self._mag_min = np.inf
        self._mag_max = -np.inf
        self.seen = 0
        self._fh = open(self.path, "wb")
        self._fh.write(_encode_blob_header(spec, self.stride))

    def add(self, sv: StepValues) -> None:
        spec = self.spec
        arr = np.asarray(sv.values, dtype=spec.dtype)
        if arr.ndim == 1:
            arr = arr.reshape(-1, 1)
        if arr.shape != (spec.n_points, spec.n_components):
            raise ValueError(
                f"Field {spec.name!r} step {sv.step_index} produced shape "
                f"{arr.shape}, expected {(spec.n_points, spec.n_components)}."
            )
        if self._transform is not None:
            arr = np.asarray(self._transform(sv.step_index, arr), dtype=spec.dtype)
        self._fh.write(arr.tobytes(order="C"))

        # Range tracking, NaN-safe so profile-restricted fields
        # don't poison the bounds.
        finite = np.isfinite(arr)
        for c in range(spec.n_components):
            col = arr[:, c][finite[:, c]]
            if col.size:
                self._comp_min[c] = min(self._comp_min[c], float(col.min()))
                self._comp_max[c] = max(self._comp_max[c], float(col.max()))

        if spec.n_components >= 3:
            mag = np.linalg.norm(arr[:, :3], axis=1)
            mag = mag[np.isfinite(mag)]
            if mag.size:
                self._mag_min = min(self._mag_min, float(mag.min()))
                self._mag_max = max(self._mag_max, float(mag.max()))
        self.seen += 1

    def close(self) -> None:
        """Close the file; safe to call more than once, and after a failure."""
        self._fh.close()

    def finish(self) -> FieldArtefactMeta:
        """Close the blob, check the step count, and return the manifest meta."""
        self.close()
        spec = self.spec
        if self.seen != spec.n_steps:
            raise ValueError(f"Field {spec.name!r} streamed {self.seen} steps but spec says {spec.n_steps}.")

        range_per_comp: dict[str, tuple[float, float]] = {}
        for c, name in enumerate(spec.components):
            if np.isfinite(self._comp_min[c]) and np.isfinite(self._comp_max[c]):
                range_per_comp[name] = (float(self._comp_min[c]), float(self._comp_max[c]))
            else:
                # All-NaN field — fall back to (0, 0) so the manifest
                # stays JSON-encodable.
                range_per_comp[name] = (0.0, 0.0)

        mag_min, mag_max = self._mag_min, self._mag_max
        if not (np.isfinite(mag_min) and np.isfinite(mag_max)):
            mag_min, mag_max = 0.0, 0.0

        return FieldArtefactMeta(
            spec=spec,
            blob_filename=self.path.name,
            stride_bytes=self.stride,
            scalar_range_per_component=range_per_comp,
            scalar_range_magnitude=(float(mag_min), float(mag_max)),
        )


def write_field_blob_streaming(
    reader: FEAStreamReader,
    spec: FieldSpec,
    out_path: os.PathLike,
    step_transform: Callable[[int, np.ndarray], np.ndarray] | None = None,
    on_step: Callable[[StepValues], None] | None = None,
) -> FieldArtefactMeta:
    """Stream one field's step-stack to disk; return the manifest meta.

    Computes the per-component and magnitude scalar ranges as steps
    pass through, so the bake never needs the full field stack in
    memory. ``step_transform(step_index, values)``, when given, rewrites
    each step before it is written and ranged (mode normalization).
    ``on_step(step_values)``, when given, is called after each step is
    written (progress).
    """

    writer = FieldBlobWriter(spec, out_path, step_transform)
    try:
        for sv in reader.iter_field_steps(spec.name):
            writer.add(sv)
            if on_step is not None:
                on_step(sv)
    finally:
        writer.close()
    return writer.finish()


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


class ElementFieldBlobWriter:
    """One (field, elem_type) bucket's AFEL blob, written a step at a time.

    The element-field counterpart of :class:`FieldBlobWriter`: per-step payload
    shape ``(n_elements, n_ips, n_components)`` float32, scalar ranges
    (per-component + magnitude over the first 3 components when the field has
    at least 3) computed inline so the manifest can pin the colour LUT across
    all steps.
    """

    def __init__(self, spec: ElementFieldSpec, out_path: os.PathLike) -> None:
        self.spec = spec
        self.path = pathlib.Path(out_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        n_components = spec.n_components
        self.stride = spec.n_elements * spec.n_ips * n_components * spec.dtype.itemsize
        self._comp_min = np.full(n_components, np.inf, dtype=np.float64)
        self._comp_max = np.full(n_components, -np.inf, dtype=np.float64)
        self._mag_min = np.inf
        self._mag_max = -np.inf
        self.seen = 0
        self._fh = open(self.path, "wb")
        self._fh.write(_encode_elem_field_blob_header(spec, self.stride))

    def add(self, sv: ElementStepValues) -> None:
        spec = self.spec
        n_components = spec.n_components
        arr = np.asarray(sv.values, dtype=spec.dtype)
        if arr.shape != (spec.n_elements, spec.n_ips, n_components):
            raise ValueError(
                f"Element field {spec.name!r}/{spec.elem_type} step "
                f"{sv.step_index} produced shape {arr.shape}, expected "
                f"{(spec.n_elements, spec.n_ips, n_components)}."
            )
        self._fh.write(np.ascontiguousarray(arr).tobytes(order="C"))

        finite = np.isfinite(arr)
        for c in range(n_components):
            col = arr[..., c][finite[..., c]]
            if col.size:
                self._comp_min[c] = min(self._comp_min[c], float(col.min()))
                self._comp_max[c] = max(self._comp_max[c], float(col.max()))

        if n_components >= 3:
            # Magnitude over the first 3 components — for stress
            # tensors this isn't the von Mises invariant but still
            # gives a sensible default colour range; the von-Mises
            # reduction is a frontend-side option.
            first3 = arr[..., :3]
            mag = np.linalg.norm(first3, axis=-1)
            mag = mag[np.isfinite(mag)]
            if mag.size:
                self._mag_min = min(self._mag_min, float(mag.min()))
                self._mag_max = max(self._mag_max, float(mag.max()))
        self.seen += 1

    def close(self) -> None:
        """Close the file; safe to call more than once, and after a failure."""
        self._fh.close()

    def finish(self) -> ElementFieldArtefactMeta:
        """Close the blob, check the step count, and return the manifest meta."""
        self.close()
        spec = self.spec
        if self.seen != spec.n_steps:
            raise ValueError(
                f"Element field {spec.name!r}/{spec.elem_type} streamed {self.seen} steps but spec says {spec.n_steps}."
            )

        range_per_comp: dict[str, tuple[float, float]] = {}
        for c, name in enumerate(spec.components):
            if np.isfinite(self._comp_min[c]) and np.isfinite(self._comp_max[c]):
                range_per_comp[name] = (float(self._comp_min[c]), float(self._comp_max[c]))
            else:
                range_per_comp[name] = (0.0, 0.0)
        mag_min, mag_max = self._mag_min, self._mag_max
        if not (np.isfinite(mag_min) and np.isfinite(mag_max)):
            mag_min, mag_max = 0.0, 0.0

        return ElementFieldArtefactMeta(
            spec=spec,
            blob_filename=self.path.name,
            stride_bytes=self.stride,
            scalar_range_per_component=range_per_comp,
            scalar_range_magnitude=(float(mag_min), float(mag_max)),
        )


def write_element_field_blob_streaming(
    reader: FEAStreamReader,
    spec: ElementFieldSpec,
    out_path: os.PathLike,
    on_step: Callable[[ElementStepValues], None] | None = None,
) -> ElementFieldArtefactMeta:
    """Stream one (field, elem_type) bucket's step-stack to disk.

    Per-step payload shape is ``(n_elements, n_ips, n_components)``
    float32. Scalar ranges (per-component + magnitude over the first
    3 components when the field has at least 3) are computed inline
    so the manifest can pin the colour LUT across all steps.
    ``on_step(step_values)``, when given, is called after each step is
    written (progress).
    """

    writer = ElementFieldBlobWriter(spec, out_path)
    try:
        for sv in reader.iter_element_field_steps(spec):
            writer.add(sv)
            if on_step is not None:
                on_step(sv)
    finally:
        writer.close()
    return writer.finish()


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
