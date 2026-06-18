"""Capacity Manager — reconstruct DNV-RP-C201 capacity models from FE results.

This subpackage reproduces what DNV Genie's *Capacity Manager* does: from a
meshed Sesam model (read via :mod:`ada.fem.formats.sesam.results.read_sin`) it
assembles *capacity models* (panel groups of plates + stiffeners), derives each
plate field's geometry / material / stiffener profile, and resolves the FE
membrane stresses into the design variables a DNV-RP-C201 buckling check needs.

The output is a *neutral*, serializable :class:`~ada.fem.capacity.model.CapacityModel`
(plus per-result-case :class:`~ada.fem.capacity.model.ResolvedCase`). adapy does
**not** run the code check itself and has **no dependency** on any code-check
package; a downstream consumer (e.g. ``aibel_dnv_rp_c201``) reads the neutral
output. A Genie-compatible ``model.json`` mirror can also be emitted for
field-by-field validation.

See ``further_work/capacity_manager_adapy_stiffened_plate.md`` in the
``dnv-rp-c201`` repo for the design and progress tracker.
"""

from __future__ import annotations

from ada.fem.capacity.manager import CapacityManager
from ada.fem.capacity.model import (
    CapacityModel,
    CapMaterial,
    CapPlate,
    CapSection,
    CapStiffener,
    ResolvedCase,
)
from ada.fem.capacity.sources import ModelJsonSource, PanelGroupSource, PanelGroupSpec, SinSource

__all__ = [
    "CapacityManager",
    "CapacityModel",
    "CapMaterial",
    "CapPlate",
    "CapSection",
    "CapStiffener",
    "ResolvedCase",
    "PanelGroupSource",
    "PanelGroupSpec",
    "ModelJsonSource",
    "SinSource",
]
