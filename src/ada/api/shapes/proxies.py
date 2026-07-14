"""Lazy shape proxies over a :class:`~ada.api.shapes.store.ShapeStore`.

``ShapeProxy`` subclasses ``Shape`` so every ``isinstance`` check, container and
export path keeps working, but it does not hold an ``ada.geom`` tree: ``.geom``
hydrates from the store's compact blob on access (weakref-cached there), so a
7k-solid import costs blobs + small records instead of millions of resident
dataclass instances. Unlike the FEM ``NodeProxy`` (millions of rows, base init
skipped), shape counts are ~10k per model, so the proxy runs the normal
``Shape.__init__`` and keeps material/placement/guid/pickle behaviour intact.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ada.api.primitives.base import Shape
from ada.base.units import Units

if TYPE_CHECKING:
    from ada.geom.core import Geometry

    from .store import ShapeStore


class ShapeProxy(Shape):
    def __init__(self, name, store: ShapeStore, index: int, **shape_kwargs):
        super().__init__(name, geom=None, **shape_kwargs)
        self._shape_store = store
        self._store_index = int(index)
        self._pinned_geom = None

    @property
    def geom(self) -> Geometry:
        """The hydrated geometry. Transient unless pinned: edits to the returned tree
        are lost when the GC reclaims it — call :meth:`pin` (or assign ``.geom``)
        before mutating."""
        if self._pinned_geom is not None:
            return self._pinned_geom
        return self._shape_store.geometry(self._store_index)

    @geom.setter
    def geom(self, value: Geometry):
        # Pin, never re-serialize: the NGEOM encoder silently skips unsupported
        # kinds, so writing back into the store could lose geometry.
        self._pinned_geom = value

    def pin(self) -> Geometry:
        """Hydrate and hold a strong reference so subsequent mutation sticks."""
        if self._pinned_geom is None:
            self._pinned_geom = self._shape_store.geometry(self._store_index)
        return self._pinned_geom

    def ngeom_blob(self):
        """The stored NGEOM buffer, for consumers that feed adacpp directly
        (tessellation, stream export) without hydrating. ``None`` when the shape
        was stored from Python-built geometry (pickle kind)."""
        return self._shape_store.ngeom_blob(self._store_index)

    def is_bare_curve(self) -> bool:
        """The stored geometry is a bare curve (wire body) — render as line geometry.
        Answered from the record, no hydration."""
        return self._shape_store.record(self._store_index).curve

    @property
    def units(self):
        return self._units

    @units.setter
    def units(self, value):
        # Mirrors Shape.units but treats the proxy as always-having-geometry: the
        # base setter keys on ``self._geom is not None``, which is always False
        # here and would silently skip the scaling.
        if isinstance(value, str):
            value = Units.from_str(value)
        if value != self._units:
            from ada.occ.utils import transform_shape

            scale_factor = Units.get_scale_factor(self._units, value)
            # transform_shape returns an OCC body; after a unit conversion the OCC
            # cache is the source of truth, exactly as on the base class.
            self._occ_cache = transform_shape(self.solid_occ(), scale_factor)

            if self.metadata.get("ifc_source") is True:
                raise NotImplementedError()

            self._units = value

    def __repr__(self):
        return f'{self.__class__.__name__}("{self.name}", store_index={self._store_index})'
