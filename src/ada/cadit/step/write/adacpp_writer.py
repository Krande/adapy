"""STEP writer backed by the adacpp CAD backend.

Mirrors the public surface of :class:`ada.cadit.step.write.writer.StepWriter`
(``add_shape`` + ``export``) but accumulates opaque adacpp shape handles and
defers the actual OCAF/XCAF STEP write to ``active_backend().write_step`` — so
STEP export works in a pure-adacpp environment with no pythonocc installed.

The OCC StepWriter and this one are selected behind ``ada.cad.doc.DocBackend``
(:class:`OccDocBackend` vs :class:`AdacppDocBackend`).
"""

from __future__ import annotations

import pathlib
from typing import Any

from ada.base.units import Units
from ada.visit.colors import Color


class AdacppStepWriter:
    # `schema` is a plain string ("AP203"/"AP214"/"AP242") rather than the
    # StepSchema enum so this module stays free of ada.cadit.step.write.writer,
    # which top-level-imports OCC (the whole point is to avoid pythonocc here).
    def __init__(self, top_level_name: str = "Assembly", units: Units = Units.M, schema: str = "AP214"):
        self.top_level_name = top_level_name
        self.units = units
        self.schema = schema if isinstance(schema, str) else schema.value
        self._shapes: list[Any] = []
        self._names: list[str] = []
        self._colors: list[tuple[float, float, float]] = []

    def add_shape(self, shape: Any, name: str, rgb_color=None, parent=None):
        # `parent` (sub-assembly nesting) is accepted for API parity with the
        # OCC StepWriter but not modelled here — adacpp.write_step builds a flat
        # assembly under one top-level label, which is all current callers need.
        if rgb_color is None:
            rgb = (1.0, 0.0, 0.0)
        elif isinstance(rgb_color, str):
            rgb = Color.from_str(rgb_color).rgb
        else:
            rgb = tuple(float(c) for c in rgb_color)
        self._shapes.append(shape)
        self._names.append(str(name))
        self._colors.append(rgb)

    def export(self, step_file: pathlib.Path | str):
        from ada.cad import active_backend

        step_file = pathlib.Path(step_file)
        step_file.parent.mkdir(parents=True, exist_ok=True)
        active_backend().write_step(
            self._shapes, self._names, self._colors, str(step_file), self.units.value, self.schema
        )
