"""Buffer-backed storage for imported shape geometry.

A large STEP/IFC import materialised as ``ada.geom`` object trees costs ~100x its
compact serialized form in resident memory (millions of small dataclass instances,
each with a ``__dict__``). ``ShapeStore`` keeps each solid as one compact blob and
hydrates the ``ada.geom.Geometry`` tree only when a consumer actually asks for it,
mirroring the FEM array-substrate design (``ada.api.mesh.store.MeshArrays``): flat
shared storage + transient proxies (``ada.api.shapes.proxies.ShapeProxy``), with a
``WeakValueDictionary`` cache so repeated access within a live scope is free and
memory returns to the blob floor when hydrated trees are dropped.

Two blob kinds:

- ``"ngeom"`` — an NGEOM buffer as produced by the adacpp native readers
  (``StepNgeomStream``). Retained exactly as it arrives (no ``bytes()`` round-trip,
  no arena append — the buffer crossing the C++ boundary is the stored object, so
  the transfer stays zero-copy). These blobs double as the tessellation/export
  fast path: adacpp consumes them directly, skipping hydrate + re-serialize.
- ``"pickle"`` — Python-built ``ada.geom`` geometry (the IFC reader, API shapes).
  The NGEOM solid encoders lower parametric solids (Box -> extruded rectangle,
  parametric profiles -> arbitrary outlines), so round-tripping Python-built
  geometry through NGEOM would be lossy; pickle keeps profiles, placements and
  ``bool_operations`` exact at a comparable byte size.

The small per-shape metadata that must stay eagerly queryable (id, color, instance
transforms/paths) lives in ``ShapeRecord`` next to the blob, not inside it — the
same split the native stream readers already make (``StepRootMeta``).
"""

from __future__ import annotations

import pickle
import weakref
import zlib
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ada.geom.core import Geometry

if TYPE_CHECKING:
    import numpy as np

    from ada.geom.booleans import BooleanOperation
    from ada.visit.colors import Color

_NGEOM_MAGIC = b"ADANGEOM"


@dataclass(slots=True)
class ShapeRecord:
    """Eager per-shape metadata — one small record per stored solid."""

    gid: str
    kind: str  # "ngeom" | "pickle"
    color: Color | None = None
    transforms: list[np.ndarray] | None = None
    instance_paths: list[tuple] | None = None
    compressed: bool = False


class ShapeStore:
    """Holds many shapes' geometry as compact blobs; hydrates on demand.

    One store per import (``from_step``/``from_ifc`` call). Proxies keep a strong
    reference to the store, so a surviving proxy keeps the (compact) blobs alive;
    hydrated ``Geometry`` trees are only weakly cached and are reclaimed by the GC
    as soon as no consumer holds them.
    """

    __slots__ = ("_blobs", "_records", "_geom_cache", "compress", "__weakref__")

    def __init__(self, compress: bool = False):
        self._blobs: list[object] = []
        self._records: list[ShapeRecord] = []
        # Mirrors MeshArrays._proxy_cache: same live object for repeated access,
        # GC'd when the last outside reference drops.
        self._geom_cache: weakref.WeakValueDictionary[int, Geometry] = weakref.WeakValueDictionary()
        self.compress = compress

    def __len__(self) -> int:
        return len(self._records)

    # --- ingest -------------------------------------------------------------------------

    def add_blob(
        self,
        blob,
        *,
        gid: str,
        color: Color | None = None,
        transforms: list[np.ndarray] | None = None,
        instance_paths: list[tuple] | None = None,
    ) -> int:
        """Retain an NGEOM buffer (bytes/memoryview/uint8 ndarray) as-arrived.

        The buffer object itself is stored — no copy — unless ``compress`` is on
        (compression inherently copies; that is the flag's accepted trade-off).
        """
        head = bytes(memoryview(blob)[:8])
        if head != _NGEOM_MAGIC:
            raise ValueError(f"not an NGEOM buffer (magic={head!r}) for {gid!r}")
        compressed = False
        if self.compress:
            blob = zlib.compress(bytes(blob), 1)
            compressed = True
        self._blobs.append(blob)
        self._records.append(
            ShapeRecord(
                gid=gid,
                kind="ngeom",
                color=color,
                transforms=transforms,
                instance_paths=instance_paths,
                compressed=compressed,
            )
        )
        return len(self._records) - 1

    def add_geometry(self, geometry: Geometry) -> int:
        """Store a Python-built ``Geometry`` losslessly (pickle kind) and drop the tree.

        The wrapper's metadata moves to the record; the pickled payload is the inner
        geometry + ``bool_operations`` (which pickle round-trips exactly, including
        half-space operands and parametric profiles the NGEOM codec would lower).
        """
        payload = pickle.dumps((geometry.geometry, geometry.bool_operations), protocol=pickle.HIGHEST_PROTOCOL)
        compressed = False
        if self.compress:
            payload = zlib.compress(payload, 1)
            compressed = True
        self._blobs.append(payload)
        self._records.append(
            ShapeRecord(
                gid=str(geometry.id),
                kind="pickle",
                color=geometry.color,
                transforms=geometry.transforms,
                instance_paths=geometry.instance_paths,
                compressed=compressed,
            )
        )
        return len(self._records) - 1

    # --- access -------------------------------------------------------------------------

    def record(self, index: int) -> ShapeRecord:
        return self._records[index]

    def ngeom_blob(self, index: int):
        """The raw NGEOM buffer for adacpp fast-path consumers (tessellation, stream
        export) — no hydration. ``None`` for pickle-kind records (those consumers
        hydrate via :meth:`geometry` and serialize with booleans on the fly)."""
        rec = self._records[index]
        if rec.kind != "ngeom":
            return None
        blob = self._blobs[index]
        if rec.compressed:
            return zlib.decompress(blob)
        return blob

    def geometry(self, index: int) -> Geometry:
        """Hydrate the full ``ada.geom.Geometry`` for one shape (weakref-cached)."""
        geom = self._geom_cache.get(index)
        if geom is not None:
            return geom
        rec = self._records[index]
        bool_ops: list[BooleanOperation] = []
        if rec.kind == "ngeom":
            from ada.cadit.ngeom.deserialize import (
                deserialize_geometries,
                promote_closed_shell,
            )

            roots = deserialize_geometries(self.ngeom_blob(index))
            if not roots:
                raise ValueError(f"NGEOM blob for {rec.gid!r} decoded to zero roots")
            # NGEOM doesn't record shell closedness; restore ClosedShell for manifold
            # B-rep roots so hydration matches the Python stream reader's form.
            inner = promote_closed_shell(roots[0][1])
        else:
            payload = self._blobs[index]
            if rec.compressed:
                payload = zlib.decompress(payload)
            inner, bool_ops = pickle.loads(payload)
        geom = Geometry(
            id=rec.gid,
            geometry=inner,
            color=rec.color,
            bool_operations=list(bool_ops),
            transforms=rec.transforms,
            instance_paths=rec.instance_paths,
        )
        self._geom_cache[index] = geom
        return geom

    # --- diagnostics / pickling -----------------------------------------------------------

    @property
    def nbytes(self) -> int:
        """Total stored blob bytes (as-held, i.e. compressed size when compressed)."""
        return sum(memoryview(b).nbytes for b in self._blobs)

    def __getstate__(self):
        # Buffer views (e.g. capsule-backed ndarrays from adacpp) coerce to bytes —
        # the one accepted pickle-time copy. The weak cache never travels.
        return {
            "blobs": [b if isinstance(b, bytes) else bytes(b) for b in self._blobs],
            "records": self._records,
            "compress": self.compress,
        }

    def __setstate__(self, state):
        self._blobs = state["blobs"]
        self._records = state["records"]
        self.compress = state["compress"]
        self._geom_cache = weakref.WeakValueDictionary()
