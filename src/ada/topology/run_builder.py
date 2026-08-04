"""Explicit, cross-section-aware construction of duct / cable-tray / pipe runs.

Where the A* router *discovers* a centreline, :class:`RunBuilder` lets you author
one directly — extend the run a given extent along a direction, turn, rise — and
it keeps the swept cross-section geometrically valid at every step (level on the
flat, the riser twist spread along the riser; see
:func:`ada.topology.routing._level_frames`).

The guiding principle is: **stay true to the input, and warn rather than deform.**
The builder lays the run exactly where you ask. When a move can't host a clean
fitting — a bend tighter than the section can turn without its inner wall
inverting, or too little straight either side of a bend for the fitting to fit —
it records a :class:`RunWarning` naming the spot and a concrete fix, instead of
silently shrinking the bend or nudging the path. The geometry is still emitted
best-effort so you can see the run; the warnings tell you where (and how) the
input needs to change to be buildable for real.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import ada
from ada.topology.routing import (
    _inversion_floor,
    _polyline_to_directrix,
    _run_segment_frames,
    _seg_len,
    _SweptRun,
)

__all__ = ["RunBuilder", "RunWarning"]


def _section_half_extents(section, open_channel: bool) -> tuple[float, float]:
    """The swept profile's true lateral (width) and up (height) half-extents. An
    open cable tray rotates the profile a quarter turn (see
    ``routing._rotate_profile_90``), so its width comes from the section's ``h``
    and its height from ``w_top``; a closed duct/box keeps them as authored."""
    if open_channel:
        return 0.5 * float(section.h or 0.0), 0.5 * float(section.w_top or 0.0)
    return 0.5 * float(section.w_top or 0.0), 0.5 * float(section.h or 0.0)


@dataclass
class RunWarning:
    """A place where the authored run can't be built as a clean fitting."""

    position: tuple[float, float, float]
    message: str
    suggestion: str

    def __str__(self) -> str:
        return f"{self.message}\n      fix: {self.suggestion}"


def _unit(d) -> tuple[float, float, float]:
    n = float((d[0] ** 2 + d[1] ** 2 + d[2] ** 2) ** 0.5)
    if n < 1e-9:
        raise ValueError("direction must be a non-zero vector")
    return (float(d[0]) / n, float(d[1]) / n, float(d[2]) / n)


class RunBuilder:
    """Author a routed run one move at a time, keeping the geometry valid.

    ::

        run = (
            RunBuilder((0, 0, 3), duct_section, name="Exhaust", seg_class="IfcDuctSegment")
            .extend((1, 0, 0), 2.0)   # 2 m along +X
            .extend((0, 1, 0), 1.5)   # turn, 1.5 m along +Y
            .extend((0, 0, 1), 1.0)   # rise 1 m
        )
        for w in run.warnings:
            logger.warning("%s", w)
        systems_part.add_objects(run.to_swept_runs())

    ``bend_radius`` defaults to ~one section width; every turn is filleted at that
    fixed radius. Feasibility is checked at :meth:`to_swept_runs` (or eagerly via
    :meth:`validate`): a bend below the section's inversion floor, or a leg too
    short to host its fittings, becomes a :class:`RunWarning`."""

    def __init__(
        self,
        origin,
        section,
        *,
        direction=None,
        bend_radius: float | None = None,
        up=(0.0, 0.0, 1.0),
        open_channel: bool = False,
        name: str = "run",
        seg_class: str = "IfcDuctSegment",
    ):
        self.name = name
        self.section = section
        self.open_channel = open_channel
        self.seg_class = seg_class
        self.up = tuple(float(c) for c in up)
        self.lateral_half, self.up_half = _section_half_extents(section, open_channel)
        self._floor = _inversion_floor(self.lateral_half, self.up_half, 0.0)
        self.bend_radius = float(bend_radius) if bend_radius else 2.0 * max(self.lateral_half, self.up_half)
        self.warnings: list[RunWarning] = []
        if self.bend_radius < self._floor - 1e-9:
            self.warnings.append(
                RunWarning(
                    tuple(float(c) for c in origin),
                    f"{self.name}: bend radius {self.bend_radius:.3g} m is below the section's "
                    f"{self._floor:.3g} m half-diagonal — every bend would invert the cross-section",
                    f"set bend_radius to at least {self._floor:.3g} m",
                )
            )
        self._pts: list[ada.Point] = [ada.Point(*(float(c) for c in origin))]
        self._legs: list[tuple[tuple[float, float, float], float]] = []  # (unit dir, extent) per leg
        self._dir = _unit(direction) if direction is not None else None

    def extend(self, direction=None, extent: float = 0.0) -> "RunBuilder":
        """Grow the run ``extent`` metres along ``direction`` (defaulting to the
        current heading). A change of direction inserts a bend at the join."""
        extent = float(extent)
        if extent <= 0.0:
            raise ValueError("extent must be positive")
        d = _unit(direction) if direction is not None else self._dir
        if d is None:
            raise ValueError("the first extend() must specify a direction")
        last = self._pts[-1]
        self._pts.append(ada.Point(last[0] + d[0] * extent, last[1] + d[1] * extent, last[2] + d[2] * extent))
        self._legs.append((d, extent))
        self._dir = d
        return self

    # Convenience directional moves --------------------------------------- #
    def rise(self, extent: float) -> "RunBuilder":
        return self.extend((0.0, 0.0, 1.0), extent)

    def drop(self, extent: float) -> "RunBuilder":
        return self.extend((0.0, 0.0, -1.0), extent)

    @property
    def points(self) -> list[ada.Point]:
        """The authored centreline (exactly as given — never simplified)."""
        return list(self._pts)

    def _tangent_at(self, i: int) -> float:
        """Straight length the bend at vertex ``i`` (between leg ``i-1`` and leg
        ``i``) consumes on each adjacent leg = ``r * tan(theta/2)``."""
        if i < 1 or i >= len(self._legs):
            return 0.0
        d0, d1 = self._legs[i - 1][0], self._legs[i][0]
        dot = max(-1.0, min(1.0, d0[0] * d1[0] + d0[1] * d1[1] + d0[2] * d1[2]))
        ang = math.acos(dot)
        return self.bend_radius * math.tan(ang / 2.0) if ang > 1e-6 else 0.0

    def validate(self) -> list[RunWarning]:
        """Re-check every bend and return the accumulated warnings. Idempotent —
        clears the bend warnings from a prior call and recomputes them (the
        radius-vs-floor warning from construction is preserved)."""
        self.warnings = [w for w in self.warnings if "invert the cross-section" in w.message]
        for i in range(1, len(self._legs)):
            tan_i = self._tangent_at(i)
            if tan_i <= 0.0:
                continue  # collinear — no bend
            v = tuple(round(float(c), 2) for c in self._pts[i])
            e_prev = self._legs[i - 1][1]
            e_next = self._legs[i][1]
            need_prev = tan_i + self._tangent_at(i - 1)  # this bend + the neighbour sharing the leg
            need_next = tan_i + self._tangent_at(i + 1)
            if e_prev < need_prev - 1e-9:
                self.warnings.append(
                    RunWarning(
                        v,
                        f"{self.name}: the {e_prev:.3g} m run before the bend at {v} is too short for a "
                        f"{self.bend_radius:.3g} m fitting (needs {need_prev:.3g} m of straight)",
                        f"extend that run to at least {need_prev:.3g} m, or reduce bend_radius",
                    )
                )
            if e_next < need_next - 1e-9:
                self.warnings.append(
                    RunWarning(
                        v,
                        f"{self.name}: the {e_next:.3g} m run after the bend at {v} is too short for a "
                        f"{self.bend_radius:.3g} m fitting (needs {need_next:.3g} m of straight)",
                        f"extend that run to at least {need_next:.3g} m, or reduce bend_radius",
                    )
                )
        return self.warnings

    def report(self) -> str:
        """A human-readable summary of the run and its geometry warnings."""
        self.validate()
        if not self.warnings:
            return f"{self.name}: OK — {len(self._legs)} legs, no geometry warnings"
        lines = [f"{self.name}: {len(self.warnings)} geometry warning(s):"]
        lines.extend(f"  - {w}" for w in self.warnings)
        return "\n".join(lines)

    def to_swept_runs(self, name: str | None = None) -> list:
        """Emit one :class:`~ada.topology.routing._SweptRun` per directrix segment
        (straight leg / arc fitting), framed continuously so the cross-section
        stays level on the flat and twists smoothly through risers. Populates
        :attr:`warnings` first (via :meth:`validate`)."""
        self.validate()
        from ada.geom.curves import IndexedPolyCurve

        name = name or self.name
        if len(self._pts) < 2:
            return []
        directrix = _polyline_to_directrix(
            self._pts, self.bend_radius, lateral_half=self.lateral_half, up_half=self.up_half
        )
        if directrix is None:
            return []
        frames = _run_segment_frames(directrix.segments, up=self.up)
        runs = []
        for i, (seg, seg_frames) in enumerate(zip(directrix.segments, frames)):
            if _seg_len(ada.Point(*seg.start), ada.Point(*seg.end)) < 1e-9:
                continue
            runs.append(
                _SweptRun(
                    f"{name}_{i}",
                    ada.Point(*seg.start),
                    ada.Point(*seg.end),
                    IndexedPolyCurve(segments=[seg]),
                    self.section,
                    open_channel=self.open_channel,
                    metadata={"segment_ifc_class": self.seg_class},
                    frames=seg_frames,
                )
            )
        return runs
