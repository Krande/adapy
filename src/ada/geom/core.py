from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Generic, TypeVar

from ada.geom.curves import CURVE_GEOM_TYPES
from ada.geom.solids import SOLID_GEOM_TYPES
from ada.geom.surfaces import SURFACE_GEOM_TYPES

if TYPE_CHECKING:
    import numpy as np

    from ada.geom.booleans import BooleanOperation
    from ada.visit.colors import Color

# Define a TypeVar that is constrained to specific geometry types
T = TypeVar("T", SOLID_GEOM_TYPES, SURFACE_GEOM_TYPES, CURVE_GEOM_TYPES)


@dataclass
class Geometry(Generic[T]):
    id: int | str
    geometry: T
    color: Color | None = None
    bool_operations: list[BooleanOperation] = field(default_factory=list)
    # Optional list of 4x4 world-placement transforms (from a STEP assembly tree) — one
    # per placed instance of this (single) solid. None = a single identity instance.
    # The solid is tessellated ONCE in its local frame; each transform is applied to the
    # resulting mesh (not the B-rep), so a part instanced N times meshes once.
    transforms: list[np.ndarray] | None = None
    # Aligned 1:1 with ``transforms``: each instance's assembly path, a root-first tuple
    # of (rep_id, product_name) levels, so a scene builder can group instances the way
    # the source assembly tree does. None = no hierarchy information.
    instance_paths: list[tuple] | None = None
