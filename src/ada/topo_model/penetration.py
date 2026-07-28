"""Penetration details where routed systems cross walls/floors.

The generic crossing geometry (:class:`~ada.topology.design_rules.Penetration`
and :func:`~ada.topology.design_rules.find_face_crossings`) lives in
``ada.topology`` and is re-exported here for backward compatibility.

This module supplies the *detail standard*: ``standard_penetration_modeller``
turns a crossing into a detail part keyed on the routing type — process runs get
a pipe sleeve, cable/electrical runs an MCT-style transit block, duct runs a
rectangular frame — and cuts the through-hole in the crossed face's built wall
plate (``face.associated_part``). ``standard_design_rules`` bundles it into a
ready :class:`~ada.topology.design_rules.DesignRules` for the engine.

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
) -> ada.Part:
    """Build the detail part for one crossing (pipe sleeve / cable block / duct
    frame by routing type) and cut the matching hole in the crossed wall plate.
    A :class:`~ada.topology.design_rules.PenetrationModeller`."""
    from ada.api.systems.base import DuctSystem, PipingSystem

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
        half = (duct_frame_size if isinstance(pen.system, DuctSystem) else cable_block_size) / 2
        in_plane = np.array([half, half, half]) * (1.0 - np.abs(n))
        lo = x - in_plane - np.abs(n) * depth / 2
        hi = x + in_plane + np.abs(n) * depth / 2
        detail = ada.PrimBox(f"{name}_block", tuple(lo), tuple(hi), color="red")
        shrink = 0.8  # the transit frame keeps a rim; the hole is the inner opening
        lo_h = x - in_plane * shrink - np.abs(n) * depth
        hi_h = x + in_plane * shrink + np.abs(n) * depth
        hole = ada.PrimBox(f"{name}_hole", tuple(lo_h), tuple(hi_h))

    _cut_wall_hole(pen, hole)
    return ada.Part(name) / detail


def standard_design_rules(
    *,
    sleeve_clearance: float = _SLEEVE_CLEARANCE,
    sleeve_wt: float = _SLEEVE_WT,
    depth: float = _DEPTH,
    cable_block_size: float = _CABLE_BLOCK_SIZE,
    duct_frame_size: float = _DUCT_FRAME_SIZE,
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
    ):
        super().__init__(systems, faces)
        self.sleeve_clearance = sleeve_clearance
        self.sleeve_wt = sleeve_wt
        self.depth = depth
        self.cable_block_size = cable_block_size
        self.duct_frame_size = duct_frame_size

    def build_penetration(self, pen: Penetration, name: str) -> ada.Part:
        return standard_penetration_modeller(
            pen,
            name,
            sleeve_clearance=self.sleeve_clearance,
            sleeve_wt=self.sleeve_wt,
            depth=self.depth,
            cable_block_size=self.cable_block_size,
            duct_frame_size=self.duct_frame_size,
        )
