"""STEP reader backed by the adacpp CAD backend.

Mirrors the consumed surface of :class:`ada.occ.step.store.StepStore`
(``iter_all_shapes`` / ``get_root_shape`` / ``get_bbox`` / ``get_num_shapes``)
but reads through ``adacpp.cad.read_step_shapes`` (OCAF names/colors) instead of
pythonocc — so STEP import works in a pure-adacpp environment. Selected behind
:class:`ada.cad.doc.AdacppDocBackend`.
"""

from __future__ import annotations

import pathlib
from dataclasses import dataclass
from typing import Any

from ada.base.units import Units
from ada.visit.colors import Color


@dataclass
class AdacppStepShape:
    """Duck-compatible with ``ada.occ.store.OccShape`` — what
    ``iter_all_shapes`` consumers read (``shape`` / ``color`` /
    ``num_tot_entities`` / ``name``)."""

    shape: Any
    color: Color | None = None
    num_tot_entities: int = 0
    name: str | None = None


class AdacppStepStore:
    def __init__(self, filepath, verbosity: bool = True, store_units: Units | str = Units.M, include_wires=False):
        self.filepath = pathlib.Path(filepath)
        self.store_units = Units.from_str(store_units) if isinstance(store_units, str) else store_units
        self.verbosity = verbosity
        self.include_wires = include_wires
        self._cache: list | None = None

    def _shapes(self) -> list:
        if self._cache is None:
            from adacpp import cad

            self._cache = list(cad.read_step_shapes(self.filepath.read_bytes()))
        return self._cache

    def iter_all_shapes(self, include_colors: bool = False):
        data = self._shapes()
        n = len(data)
        for d in data:
            color = Color(*d.color) if (include_colors and d.has_color) else None
            yield AdacppStepShape(d.shape, color, n, d.name or None)

    def get_num_shapes(self, *args, **kwargs) -> int:
        return len(self._shapes())

    def get_root_shape(self, use_ocaf: bool = False):
        from adacpp import cad

        return cad.read_step_bytes(self.filepath.read_bytes())

    def get_bbox(self):
        from ada.cad import active_backend

        return active_backend().bbox(self.get_root_shape())
