from __future__ import annotations

import reprlib
from itertools import chain
from operator import attrgetter
from typing import TYPE_CHECKING, Dict, Iterable, Union

from ada.api.containers.base import NumericMapped
from ada.base.units import Units
from ada.core.utils import Counter
from ada.materials import Material
from ada.materials.metals.base_models import MaterialDampingRayleigh

if TYPE_CHECKING:
    from ada import Assembly, Part


class Materials(NumericMapped):
    """Collection of materials"""

    def __init__(self, materials: Iterable[Material] = None, parent: Union[Part, Assembly] = None, units=Units.M):
        super().__init__(parent)
        self.materials = sorted(materials, key=attrgetter("name")) if materials is not None else []
        self.recreate_name_and_id_maps(self.materials)
        self._units = units

    def __contains__(self, item: Material):
        return item.name in self._name_map.keys()

    def __len__(self) -> int:
        return len(self.materials)

    def __iter__(self) -> Iterable[Material]:
        return iter(self.materials)

    def __getitem__(self, index):
        result = self.materials[index]
        return Materials(result) if isinstance(index, slice) else result

    def __eq__(self, other: Materials):
        if not isinstance(other, Materials):
            return NotImplemented
        return self.materials == other.materials

    def __ne__(self, other: Materials):
        if not isinstance(other, Materials):
            return NotImplemented
        return self.materials != other.materials

    def __add__(self, other: Materials):
        if self.parent is None:
            raise ValueError("Parent cannot be zero")
        for mat in other:
            mat.parent = self.parent
        other.renumber_id(self.max_id + 1)
        return Materials(chain(self, other), parent=self.parent)

    def __repr__(self):
        rpr = reprlib.Repr()
        rpr.maxlist = 8
        rpr.maxlevel = 1
        return f"Materials({rpr.repr(self.materials) if self.materials else ''})"

    def merge_materials_by_properties(self):
        models = {}  # Use dict for O(1) lookup
        final_mats = []

        for i, mat in enumerate(self.materials):
            up = tuple(mat.model.unique_props())
            prop_key = self._make_hashable_key(up)

            if prop_key not in models:
                models[prop_key] = len(final_mats)  # Store index
                final_mats.append(mat)
            else:
                index = models[prop_key]
                replacement_mat = final_mats[index]
                for ref in mat.refs:
                    ref.material = replacement_mat

        self.materials = final_mats
        self.recreate_name_and_id_maps(self.materials)

    def _make_hashable_key(self, props_tuple):
        """Convert properties tuple to a hashable key, handling numpy arrays"""
        hashable_items = []

        for prop in props_tuple:
            if prop is None:
                hashable_items.append(None)
            elif hasattr(prop, "__len__") and not isinstance(prop, str):
                # Handle numpy arrays and lists
                try:
                    import numpy as np

                    if isinstance(prop, np.ndarray):
                        # Convert to tuple of values for hashing
                        hashable_items.append(tuple(prop.flatten()))
                    elif isinstance(prop, (list, tuple)):
                        # Convert lists to tuples recursively
                        hashable_items.append(tuple(prop))
                    else:
                        # Fallback for other iterable types
                        hashable_items.append(tuple(prop))
                except (TypeError, ValueError):
                    # If conversion fails, use string representation
                    hashable_items.append(str(prop))
            elif isinstance(prop, MaterialDampingRayleigh):
                hashable_items.append((prop.alpha, prop.beta))
            else:
                # Scalar values are already hashable
                hashable_items.append(prop)

        return tuple(hashable_items)

    def index(self, item: Material):
        return self.materials.index(item)

    def count(self, item: Material):
        return int(item in self)

    def get_by_name(self, name: str) -> Material:
        if name not in self._name_map.keys():
            raise ValueError(f'The material name "{name}" is not found')
        else:
            return self._name_map[name]

    def get_by_id(self, mat_id: int) -> Material:
        if mat_id not in self._id_map.keys():
            raise ValueError(f'The material id "{mat_id}" is not found')
        else:
            return self._id_map[mat_id]

    def renumber_id(self, start_id=1):
        cnt = Counter(start=start_id)
        for mat_id in sorted(self.id_map.keys()):
            mat = self.get_by_id(mat_id)
            mat.id = next(cnt)
        self.recreate_name_and_id_maps(self.materials)

    @property
    def name_map(self) -> Dict[str, Material]:
        return self._name_map

    @property
    def id_map(self) -> Dict[int, Material]:
        return self._id_map

    @property
    def units(self):
        return self._units

    @units.setter
    def units(self, value):
        if isinstance(value, str):
            value = Units.from_str(value)
        if value != self._units:
            for m in self.materials:
                m.units = value
            self._units = value

    def add(self, material: Material) -> Material:
        name_map = self._name_map
        id_map = self._id_map
        mats = self.materials

        # 1) Fast-path existing: use dict.get instead of “in self” or keys()
        existing = name_map.get(material.name)
        if existing is not None:
            # merge refs in one pass, avoiding O(n²) list lookups
            existing_refs = existing.refs
            # build a set for O(1) membership tests
            seen = set(existing_refs)
            # only append the new ones
            for ref in material.refs:
                if ref not in seen:
                    existing_refs.append(ref)
                    seen.add(ref)
            return existing

        # 2) Assign a fresh id if needed
        mat_id = material.id
        if mat_id is None or mat_id in id_map:
            mat_id = len(mats) + 1
            material.id = mat_id

        # 3) Insert in O(1)
        mats.append(material)
        id_map[mat_id] = material
        name_map[material.name] = material

        return material
