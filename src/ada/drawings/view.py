"""View definition: where the camera looks from, the drawing scale and the section/depth planes.

A drawing is an orthographic projection. The view's ``direction`` is the look direction (from
the viewer into the scene) and ``up`` the model direction that points up the sheet. Screen
coordinates are ``x = (P - origin) . right``, ``y = (P - origin) . up`` and the depth
``z = (P - origin) . direction`` (larger is further away), with ``right = direction x up``.

Named views assume Z up:

========  ======================  ===============
name      looks along             sheet right is
========  ======================  ===============
front     +Y (viewer at -Y)       +X
back      -Y                      -X
right     -X (viewer at +X)       +Y
left      +X                      -Y
top/plan  -Z                      +X (+Y up the sheet)
bottom    +Z                      +X (-Y up the sheet)
iso       (-1, +1, -1)            (+1, +1, 0)
========  ======================  ===============
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Sequence

import numpy as np

ViewName = Literal["front", "back", "left", "right", "top", "plan", "bottom", "iso"]
Units = Literal["m", "mm"]

_NAMED: dict[str, tuple[tuple[float, float, float], tuple[float, float, float]]] = {
    "front": ((0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
    "back": ((0.0, -1.0, 0.0), (0.0, 0.0, 1.0)),
    "right": ((-1.0, 0.0, 0.0), (0.0, 0.0, 1.0)),
    "left": ((1.0, 0.0, 0.0), (0.0, 0.0, 1.0)),
    "top": ((0.0, 0.0, -1.0), (0.0, 1.0, 0.0)),
    "plan": ((0.0, 0.0, -1.0), (0.0, 1.0, 0.0)),
    "bottom": ((0.0, 0.0, 1.0), (0.0, -1.0, 0.0)),
    "iso": ((-1.0, 1.0, -1.0), (0.0, 0.0, 1.0)),
}

#: Preferred scale denominators, used when a drawing picks its own scale.
STANDARD_SCALES = (1, 2, 5, 10, 20, 25, 50, 75, 100, 125, 150, 200, 250, 500, 1000, 2000, 2500, 5000, 10000)


def _unit(v: Sequence[float]) -> np.ndarray:
    a = np.asarray(v, dtype=float)
    n = np.linalg.norm(a)
    if n < 1e-12:
        raise ValueError(f"zero-length direction {tuple(v)}")
    return a / n


@dataclass(frozen=True)
class SectionPlane:
    """A cutting plane. Material on the side ``normal`` points to is kept (drawn beyond the cut);
    the rest is removed and the plane's intersection with the solids is drawn as cut linework.

    For a section seen face-on, ``normal`` is the view direction (the default when a drawing
    builds the plane from a depth with :meth:`View.section_at`).
    """

    origin: tuple[float, float, float]
    normal: tuple[float, float, float]

    def unit_normal(self) -> np.ndarray:
        return _unit(self.normal)

    def signed_distance(self, pts: np.ndarray) -> np.ndarray:
        return (np.asarray(pts, float) - np.asarray(self.origin, float)) @ self.unit_normal()


@dataclass(frozen=True)
class View:
    """An orthographic camera.

    ``depth`` limits what is drawn to ``near <= (P - origin) . direction <= far`` (either may be
    ``None``); it clips without drawing cut lines. Use a :class:`SectionPlane` on the drawing
    for a cut.
    """

    direction: tuple[float, float, float] = (0.0, 1.0, 0.0)
    up: tuple[float, float, float] = (0.0, 0.0, 1.0)
    origin: tuple[float, float, float] = (0.0, 0.0, 0.0)
    depth: tuple[float | None, float | None] = (None, None)
    name: str | None = None

    @classmethod
    def named(
        cls,
        name: ViewName,
        origin: tuple[float, float, float] = (0.0, 0.0, 0.0),
        depth: tuple[float | None, float | None] = (None, None),
    ) -> "View":
        if name not in _NAMED:
            raise ValueError(f"unknown view {name!r}; expected one of {sorted(_NAMED)}")
        d, u = _NAMED[name]
        return cls(direction=d, up=u, origin=origin, depth=depth, name=name)

    def basis(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """(right, up, direction) as orthonormal vectors."""
        d = _unit(self.direction)
        u = np.asarray(self.up, float)
        u = u - (u @ d) * d
        if np.linalg.norm(u) < 1e-9:  # up parallel to the look direction: pick any perpendicular
            u = np.array([0.0, 0.0, 1.0]) if abs(d[2]) < 0.9 else np.array([0.0, 1.0, 0.0])
            u = u - (u @ d) * d
        u = _unit(u)
        r = np.cross(d, u)
        return r, u, d

    def to_screen(self, pts) -> np.ndarray:
        """Model points (N, 3) -> screen (N, 3) as (x right, y up, z depth), model units."""
        p = np.atleast_2d(np.asarray(pts, float)) - np.asarray(self.origin, float)
        r, u, d = self.basis()
        return np.stack([p @ r, p @ u, p @ d], axis=1)

    def section_at(self, point: tuple[float, float, float]) -> SectionPlane:
        """A section plane through ``point`` facing this view (material beyond it is kept)."""
        return SectionPlane(origin=tuple(float(c) for c in point), normal=tuple(float(c) for c in self.direction))

    def clip_planes(self) -> list[SectionPlane]:
        r, u, d = self.basis()
        o = np.asarray(self.origin, float)
        planes = []
        near, far = self.depth
        if near is not None:
            planes.append(SectionPlane(tuple(o + d * near), tuple(d)))
        if far is not None:
            planes.append(SectionPlane(tuple(o + d * far), tuple(-d)))
        return planes


def mm_per_unit(units: Units) -> float:
    return {"m": 1000.0, "mm": 1.0}[units]


@dataclass
class Projector:
    """Model -> paper (millimetres, y down) for a view at a scale ``1:scale``."""

    view: View
    scale: float
    units: Units = "m"
    _k: float = field(init=False)

    def __post_init__(self):
        self._k = mm_per_unit(self.units) / float(self.scale)

    @property
    def k(self) -> float:
        """Paper millimetres per model unit."""
        return self._k

    def screen_to_paper(self, xy) -> np.ndarray:
        a = np.atleast_2d(np.asarray(xy, float))
        return np.stack([a[:, 0] * self._k, -a[:, 1] * self._k], axis=1)

    def to_paper(self, pts) -> np.ndarray:
        return self.screen_to_paper(self.view.to_screen(pts)[:, :2])

    def point(self, p) -> np.ndarray:
        return self.to_paper(p)[0]


def pick_scale(extent_units: tuple[float, float], max_paper_mm: tuple[float, float], units: Units) -> float:
    """Smallest standard denominator that fits ``extent_units`` (w, h) inside ``max_paper_mm``."""
    w, h = max(extent_units[0], 1e-9), max(extent_units[1], 1e-9)
    mpu = mm_per_unit(units)
    for s in STANDARD_SCALES:
        if w * mpu / s <= max_paper_mm[0] and h * mpu / s <= max_paper_mm[1]:
            return float(s)
    return float(STANDARD_SCALES[-1])
