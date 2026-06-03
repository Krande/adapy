from __future__ import annotations

from operator import attrgetter
from typing import TYPE_CHECKING, Iterable

from ada.api.containers.base import IndexedCollection
from ada.api.plates.base_pl import Plate

if TYPE_CHECKING:
    from ada import Part


class Plates(IndexedCollection[Plate, str, int]):
    def __init__(self, plates: Iterable[Plate] = (), parent: Part = None):
        # attrgetter rather than lambdas so the collection round-trips
        # through pickle; closure-scoped lambdas have no qualified name.
        super().__init__(
            items=plates,
            sort_key=attrgetter("name"),
            id_key=attrgetter("guid"),
            name_key=attrgetter("name"),
        )
        self._parent = parent

    def add(self, plate: Plate) -> Plate:
        if plate.name is None:
            raise ValueError("Name may not be None")
        existing = self._nmap.get(plate.name)
        if existing:
            return existing
        # handle material as before…
        mat = self._parent.materials.add(plate.material)
        if mat is not None:
            plate.material = mat

        super().add(plate)
        return plate
