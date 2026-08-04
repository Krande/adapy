"""Penetration details where routed systems cross walls/floors.

The generic crossing geometry (:class:`~ada.topology.design_rules.Penetration`
and :func:`~ada.topology.design_rules.find_face_crossings`) lives in
``ada.topology`` and is re-exported here for backward compatibility.

This module supplies the *detail standard*: ``standard_penetration_modeller``
turns a crossing into a detail part keyed on the routing type — a process (pipe)
run gets a round sleeve + circular hole; a cable-tray/duct run gets a RECTANGULAR
frame + hole sized to the run's cross-section (width x height) plus a per-side
tolerance, oriented width along the lateral axis and height along the vertical —
and cuts the through-hole in the crossed face's built wall plate
(``face.associated_part``). ``standard_design_rules`` bundles it into a ready
:class:`~ada.topology.design_rules.DesignRules` for the engine.

``PenetrationBlueprintBase`` / ``StandardPenetrations`` remain as the subclass-
based scaffold for callers that build penetrations directly.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

import ada
from ada.topology import BlueprintBase
from ada.topology.design_rules import DesignRules, Penetration, find_face_crossings

if TYPE_CHECKING:
    from ada.api.systems.base import System
    from ada.topology.graph import GraphFace

__all__ = [
    "Penetration",
    "PenetrationBlueprintBase",
    "StandardPenetrations",
    "find_face_crossings",
    "standard_penetration_modeller",
    "standard_design_rules",
]

# Standard detail dimensions (metres). Kept module-level so both the modeller
# function and StandardPenetrations share one source of truth.
_SLEEVE_CLEARANCE = 0.02
_SLEEVE_WT = 8e-3
_DEPTH = 0.3
_CABLE_BLOCK_SIZE = 0.3
_DUCT_FRAME_SIZE = 0.45
# Per-side tolerance (metres) added around a tray/duct's true cross-section when
# cutting its RECTANGULAR through-hole (width + 2*tol by height + 2*tol). Module
# level so callers can override the default via the modeller/ruleset kwargs.
_TRAY_DUCT_CLEARANCE = 0.02
# Visible transit-frame rim (metres) grown around the rectangular hole so the
# detail part reads as a framed opening rather than sitting flush with the cut.
_FRAME_RIM = 0.05


def _rect_section_wh(system) -> tuple[float, float]:
    """The routed run's rectangular cross-section ``(width, height)`` in metres:
    a duct uses ``duct_width/duct_height``; a cable/electrical tray uses
    ``tray_width/tray_height``. Falls back to the legacy fixed block/frame size
    when a section attribute is absent."""
    from ada.api.systems.base import DuctSystem

    if isinstance(system, DuctSystem):
        w = float(getattr(system, "duct_width", _DUCT_FRAME_SIZE))
        h = float(getattr(system, "duct_height", _DUCT_FRAME_SIZE))
    else:  # CableSystem / ElectricalSystem tray
        w = float(getattr(system, "tray_width", _CABLE_BLOCK_SIZE))
        h = float(getattr(system, "tray_height", _CABLE_BLOCK_SIZE))
    return w, h


def _rect_axes(n: np.ndarray) -> tuple[int, int]:
    """Given the (axis-aligned) face normal, return ``(width_axis, height_axis)``:
    the two in-plane axes with the rectangle's WIDTH along the horizontal lateral
    axis and its HEIGHT along the vertical (global Z) — so a tray/duct opening is
    wider than tall, matching the section. When the face normal is itself vertical
    (a floor/roof crossing) neither in-plane axis is Z, so the two in-plane axes
    are used in order."""
    normal_axis = int(np.argmax(np.abs(n)))
    in_plane = [a for a in range(3) if a != normal_axis]
    if 2 in in_plane:  # a wall: keep height along the vertical axis
        height_axis = 2
        width_axis = next(a for a in in_plane if a != 2)
    else:  # a floor/roof: no vertical in-plane axis
        width_axis, height_axis = in_plane[0], in_plane[1]
    return width_axis, height_axis


def _cut_wall_hole(pen: Penetration, hole: ada.Shape) -> None:
    """Cut the through-opening in the crossed face's built wall plate(s), when
    the face carries one (``face.associated_part``)."""
    wall_part = pen.face.associated_part
    if wall_part is None:
        return
    for pl in wall_part.get_all_physical_objects(by_type=ada.Plate):
        pl.add_boolean(hole)


def standard_penetration_modeller(
    pen: Penetration,
    name: str,
    *,
    sleeve_clearance: float = _SLEEVE_CLEARANCE,
    sleeve_wt: float = _SLEEVE_WT,
    depth: float = _DEPTH,
    cable_block_size: float = _CABLE_BLOCK_SIZE,
    duct_frame_size: float = _DUCT_FRAME_SIZE,
    tray_duct_clearance: float = _TRAY_DUCT_CLEARANCE,
) -> ada.Part:
    """Build the detail part for one crossing and cut the matching hole in the
    crossed wall plate. A pipe run gets a round sleeve + circular hole; a
    cable-tray/duct run gets a RECTANGLE sized to the run's cross-section
    (width x height) plus ``tray_duct_clearance`` on each side, oriented width
    along the lateral axis and height along the vertical. A
    :class:`~ada.topology.design_rules.PenetrationModeller`."""
    from ada.api.systems.base import PipingSystem

    n = np.asarray(tuple(pen.normal), dtype=float)
    n /= np.linalg.norm(n)
    x = np.asarray(tuple(pen.point), dtype=float)
    p1 = tuple(x - n * depth / 2)
    p2 = tuple(x + n * depth / 2)

    if isinstance(pen.system, PipingSystem):
        hole_r = pen.system.pipe_radius + sleeve_clearance
        detail: ada.Shape = ada.PrimCyl(f"{name}_sleeve", p1, p2, hole_r + sleeve_wt, color="red")
        hole = ada.PrimCyl(f"{name}_hole", p1, p2, hole_r)
    else:
        # Rectangular cut sized to the tray/duct section + tolerance each side.
        sec_w, sec_h = _rect_section_wh(pen.system)
        width_axis, height_axis = _rect_axes(n)
        hole_half = np.zeros(3)
        hole_half[width_axis] = sec_w / 2 + tray_duct_clearance
        hole_half[height_axis] = sec_h / 2 + tray_duct_clearance
        # Cut fully through the plate along the normal (±depth); the visible frame
        # spans ±depth/2 and grows a rim in-plane so it reads as a framed opening.
        thru = np.abs(n) * depth
        lo_h = x - hole_half - thru
        hi_h = x + hole_half + thru
        hole = ada.PrimBox(f"{name}_hole", tuple(lo_h), tuple(hi_h))

        frame_half = hole_half.copy()
        frame_half[width_axis] += _FRAME_RIM
        frame_half[height_axis] += _FRAME_RIM
        lo = x - frame_half - np.abs(n) * depth / 2
        hi = x + frame_half + np.abs(n) * depth / 2
        detail = ada.PrimBox(f"{name}_frame", tuple(lo), tuple(hi), color="red")

    _cut_wall_hole(pen, hole)
    return ada.Part(name) / detail


def standard_design_rules(
    *,
    sleeve_clearance: float = _SLEEVE_CLEARANCE,
    sleeve_wt: float = _SLEEVE_WT,
    depth: float = _DEPTH,
    cable_block_size: float = _CABLE_BLOCK_SIZE,
    duct_frame_size: float = _DUCT_FRAME_SIZE,
    tray_duct_clearance: float = _TRAY_DUCT_CLEARANCE,
) -> DesignRules:
    """A :class:`~ada.topology.design_rules.DesignRules` with default routing and
    the standard penetration detail (:func:`standard_penetration_modeller`).
    This is the ruleset the demo and the viewer compile use."""

    def model_penetration(pen: Penetration, name: str) -> ada.Part:
        return standard_penetration_modeller(
            pen,
            name,
            sleeve_clearance=sleeve_clearance,
            sleeve_wt=sleeve_wt,
            depth=depth,
            cable_block_size=cable_block_size,
            duct_frame_size=duct_frame_size,
            tray_duct_clearance=tray_duct_clearance,
        )

    return DesignRules(model_penetration=model_penetration)


class PenetrationBlueprintBase(BlueprintBase):
    """Blueprint scaffold: crossings of ``systems`` x ``faces`` become detail
    parts, grouped per system. Subclasses implement ``build_penetration``."""

    def __init__(self, systems: list[System], faces: list[GraphFace]):
        super().__init__()
        self.systems = list(systems)
        self.faces = list(faces)
        self.penetrations: list[Penetration] = []

    def _group_prefix(self) -> str:
        return "Penetrations"

    def find_penetrations(self) -> list[Penetration]:
        out: list[Penetration] = []
        for system in self.systems:
            out.extend(find_face_crossings(system, self.faces))
        return out

    def build_penetration(self, pen: Penetration, name: str) -> ada.Part:
        raise NotImplementedError("subclasses implement the penetration detail")

    def build(self) -> ada.Part:
        self.output_part = ada.Part("Penetrations")
        self.penetrations = self.find_penetrations()
        counts: dict[str, int] = {}
        for pen in self.penetrations:
            i = counts.get(pen.system.name, 0)
            counts[pen.system.name] = i + 1
            self.add_to_area(pen.system.name, self.build_penetration(pen, f"{pen.system.name}_pen_{i:02d}"))
        self.load_parts_from_area_map()
        return self.output_part


class StandardPenetrations(PenetrationBlueprintBase):
    """Reference detail standard by routing type (see module docstring).
    Thin wrapper over :func:`standard_penetration_modeller`."""

    def __init__(
        self,
        systems: list[System],
        faces: list[GraphFace],
        sleeve_clearance: float = _SLEEVE_CLEARANCE,
        sleeve_wt: float = _SLEEVE_WT,
        depth: float = _DEPTH,
        cable_block_size: float = _CABLE_BLOCK_SIZE,
        duct_frame_size: float = _DUCT_FRAME_SIZE,
        tray_duct_clearance: float = _TRAY_DUCT_CLEARANCE,
    ):
        super().__init__(systems, faces)
        self.sleeve_clearance = sleeve_clearance
        self.sleeve_wt = sleeve_wt
        self.depth = depth
        self.cable_block_size = cable_block_size
        self.duct_frame_size = duct_frame_size
        self.tray_duct_clearance = tray_duct_clearance

    def build_penetration(self, pen: Penetration, name: str) -> ada.Part:
        return standard_penetration_modeller(
            pen,
            name,
            sleeve_clearance=self.sleeve_clearance,
            sleeve_wt=self.sleeve_wt,
            depth=self.depth,
            cable_block_size=self.cable_block_size,
            duct_frame_size=self.duct_frame_size,
            tray_duct_clearance=self.tray_duct_clearance,
        )
