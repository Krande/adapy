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


def _crossing_legs(routed_path, point) -> tuple:
    """``(tangent, feed_dir)`` for a crossing: the unit direction of the polyline
    segment the point lies on (the run's travel through the face), and the unit
    direction of the horizontal leg *feeding* that segment. The feed leg is the
    nearest horizontal segment walking OUTWARD from the crossing segment in path
    order — NOT the globally nearest by midpoint, which can pick a parallel leg
    elsewhere in the run. On a riser through a deck the tray/duct is framed off this
    adjacent leg (routes are twist-free, so both sides of the riser share it), so it
    fixes both the cut orientation and the profile-centroid shift. Either element is
    ``None`` when the path is empty / has no horizontal leg."""
    if not routed_path:
        return None, None
    pts = [np.asarray(tuple(p), dtype=float) for p in routed_path]
    x = np.asarray(tuple(point), dtype=float)
    segs = list(zip(pts[:-1], pts[1:]))

    ci, t, best = None, None, float("inf")
    for i, (a, b) in enumerate(segs):
        d = b - a
        length = float(np.linalg.norm(d))
        if length < 1e-9:
            continue
        u = float(np.clip((x - a) @ d / (d @ d), 0.0, 1.0))
        dist = float(np.linalg.norm(a + u * d - x))
        if dist < best:
            best, ci, t = dist, i, d / length
    if ci is None:
        return None, None

    def _hdir(seg):
        d = seg[1] - seg[0]
        if abs(d[2]) <= 1e-6 and np.linalg.norm(d[:2]) > 1e-9:
            return d / np.linalg.norm(d)
        return None

    feed = _hdir(segs[ci])  # the crossing segment itself, if it's horizontal (a wall)
    if feed is None:  # a riser: take the nearest horizontal leg adjacent in path order
        for off in range(1, len(segs) + 1):
            for j in (ci - off, ci + off):
                if 0 <= j < len(segs):
                    h = _hdir(segs[j])
                    if h is not None:
                        feed = h
                        break
            if feed is not None:
                break
    return t, feed


def _feed_axis(routed_path, point) -> int | None:
    """The dominant horizontal axis (0=X, 1=Y) of the leg feeding the crossing. On a
    riser the tray keeps its width (lateral) perpendicular to this leg and its
    opening/height along it, so the deck cutout is oriented from it. ``None`` when
    the run has no horizontal leg."""
    _t, feed = _crossing_legs(routed_path, point)
    return None if feed is None else int(np.argmax(np.abs(feed[:2])))


def _crossing_up(system, point) -> np.ndarray:
    """The run's profile local +y (its "up" / opening direction) at a crossing,
    computed like the sweep framing (``dir_x = tangent x world-up``,
    ``dir_y = dir_x x tangent``; on a vertical riser the lateral comes from the
    feeding horizontal leg). Used to shift a cut onto the profile centroid — an open
    cable tray sits ENTIRELY on the +y side of its directrix (web on the route,
    opening up), so its hole must move half a tray-height that way to line up with
    the tray body. Falls back to +Z."""
    up = np.array([0.0, 0.0, 1.0])
    t, feed = _crossing_legs(getattr(system, "routed_path", None), point)
    if t is None:
        return up
    lat = np.cross(t, up)
    if np.linalg.norm(lat) < 1e-6:  # vertical (deck) crossing: lateral from the feed leg
        if feed is None:
            return up
        lat = np.cross(feed, up)
    lat = lat / np.linalg.norm(lat)
    dy = np.cross(lat, t)
    ndy = float(np.linalg.norm(dy))
    return dy / ndy if ndy > 1e-9 else up


def _rect_axes(n: np.ndarray, feed_axis: int | None = None) -> tuple[int, int]:
    """Given the (axis-aligned) face normal, return ``(width_axis, height_axis)``:
    the two in-plane axes with the rectangle's WIDTH along the horizontal lateral
    axis and its HEIGHT along the vertical (global Z) — so a tray/duct opening is
    wider than tall, matching the section. When the face normal is itself vertical
    (a floor/roof crossing) neither in-plane axis is Z: the run travels vertically
    through the deck, so its HEIGHT (opening) lies along the feeding horizontal leg
    (``feed_axis``) and its WIDTH (lateral) along the perpendicular horizontal axis.
    Without a ``feed_axis`` the two in-plane axes are used in order (legacy)."""
    normal_axis = int(np.argmax(np.abs(n)))
    in_plane = [a for a in range(3) if a != normal_axis]
    if 2 in in_plane:  # a wall: keep height along the vertical axis
        height_axis = 2
        width_axis = next(a for a in in_plane if a != 2)
    elif feed_axis is not None and feed_axis in in_plane:  # a deck: orient from travel
        height_axis = feed_axis  # opening rotates onto the travel axis up the riser
        width_axis = next(a for a in in_plane if a != feed_axis)
    else:  # a floor/roof with no known travel: in-order fallback
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
    from ada.api.systems.base import CableSystem, PipingSystem

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
        # For a deck (vertical normal) the in-plane orientation follows the run's
        # travel, so the rectangle isn't rotated 90 deg against the tray.
        feed_axis = _feed_axis(getattr(pen.system, "routed_path", None), pen.point)
        width_axis, height_axis = _rect_axes(n, feed_axis)
        # A cable tray's open channel sits on the +y (opening) side of its directrix,
        # so its body centre is half a tray-height off the route — shift the cut and
        # frame there so they line up with the tray rather than hanging half off it.
        # A duct's box profile is centred on the route (no shift).
        center = x.copy()
        if isinstance(pen.system, CableSystem):
            center = x + (sec_h / 2.0) * _crossing_up(pen.system, pen.point)
        hole_half = np.zeros(3)
        hole_half[width_axis] = sec_w / 2 + tray_duct_clearance
        hole_half[height_axis] = sec_h / 2 + tray_duct_clearance
        # Cut fully through the plate along the normal (±depth); the visible frame
        # spans ±depth/2 and grows a rim in-plane so it reads as a framed opening.
        thru = np.abs(n) * depth
        lo_h = center - hole_half - thru
        hi_h = center + hole_half + thru
        hole = ada.PrimBox(f"{name}_hole", tuple(lo_h), tuple(hi_h))

        frame_half = hole_half.copy()
        frame_half[width_axis] += _FRAME_RIM
        frame_half[height_axis] += _FRAME_RIM
        lo = center - frame_half - np.abs(n) * depth / 2
        hi = center + frame_half + np.abs(n) * depth / 2
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
