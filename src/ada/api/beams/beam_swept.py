from __future__ import annotations

from typing import TYPE_CHECKING, Iterable

import numpy as np

from ada.api.curves import CurveOpen2d, CurveOpen3d
from ada.geom import Geometry

from .base_bm import Beam

if TYPE_CHECKING:
    from ada import Section
    from ada.geom.curves import CURVE_GEOM_TYPES
    from ada.geom.solids import FixedReferenceSweptAreaSolid


class BeamSweep(Beam):
    """A beam whose section is swept along a path of straight legs and circular arcs.

    One solid member for a bent tube, a rolled section or any other curved member, instead of a
    chain of straight beams.

    ``curve`` (the sweep path, in the beam's local system) may be

    * a :class:`~ada.api.curves.CurveOpen3d` -- 3D corner points, a corner carrying a 4th value is
      rounded with that bend radius: ``[(0, 0, 0), (0, 0, -8, 5.0), (8, 0, -8)]`` is a vertical
      leg, a 90 degree bend of radius 5 and a horizontal leg;
    * a :class:`~ada.api.curves.CurveOpen2d` / ``CurvePoly2d`` (a planar curve, the same corner
      notation in its own plane);
    * an ``ada.geom.curves`` path -- an ``IndexedPolyCurve`` of ``Edge`` and ``ArcLine``
      (start, a point on the arc, end) segments, or a single ``Edge``/``ArcLine`` -- when the arcs
      are known exactly;
    * a plain list of points (with optional 4th bend radius), read as a ``CurveOpen3d``.

    The section is placed normal to the path at its start and carried along the path without twist
    (a rotation-minimising frame; on a planar path its in-plane axis stays in the plane). As for a
    straight beam, section local +y lies along ``up x tangent`` and section local +z along ``up``
    projected normal to the start tangent; ``up`` defaults to the straight beam's default for the
    start-to-end chord. A tubular section needs no ``up``.

    Unlike a straight :class:`Beam`, end eccentricities/offsets are not applied to the swept solid.
    """

    def __init__(
        self,
        name: str,
        curve: CurveOpen2d | CurveOpen3d | CURVE_GEOM_TYPES | Iterable[Iterable[float]],
        sec: str | Section,
        up=None,
        **kwargs,
    ):
        curve = _as_sweep_path(curve)
        directrix = _directrix_of(curve)

        from ada.geom.sweep_frames import directrix_segments

        segs = directrix_segments(directrix)
        n1 = np.asarray(list(segs[0].start), dtype=float)
        n2 = np.asarray(list(segs[-1].end), dtype=float)

        # The straight-beam orientation is derived from the start-to-end chord; an ``up`` parallel
        # to that chord is still a valid sweep reference (it only has to differ from the START
        # tangent), so keep it for the sweep and let the chord frame take its default.
        chord = n2 - n1
        chord_up = up
        if up is not None:
            u = np.asarray(list(up), dtype=float)
            cn = float(np.linalg.norm(chord))
            if cn < 1e-12 or float(np.linalg.norm(np.cross(u, chord / cn))) < 1e-6:
                chord_up = None
        super().__init__(name=name, n1=n1, n2=n2, sec=sec, up=chord_up, **kwargs)
        self._sweep_up = None if up is None else np.asarray(list(up), dtype=float)
        self._curve = curve
        self._directrix = directrix
        if isinstance(curve, (CurveOpen2d, CurveOpen3d)):
            curve._parent = self

    @property
    def curve(self) -> CurveOpen2d | CurveOpen3d | CURVE_GEOM_TYPES:
        """The sweep path as given (a list of points is wrapped in a ``CurveOpen3d``)."""
        return self._curve

    @property
    def directrix(self) -> CURVE_GEOM_TYPES:
        """The sweep path as an ``ada.geom`` curve of ``Edge``/``ArcLine`` segments."""
        return self._directrix

    def sweep_frames(self, max_arc_step_deg: float | None = None):
        """``(origins, dir_x, dir_y)``: the stations the section is placed at along the path.

        Section local +y maps to ``dir_x`` and local +z to ``dir_y``; ``dir_x x dir_y`` is along
        the path tangent. Arcs are sampled at most ``max_arc_step_deg`` (default 5) apart; a
        sharp corner (a path point without a bend radius) is a mitre joint."""
        from ada.geom.sweep_frames import (
            DEFAULT_MAX_ARC_STEP_DEG,
            sample_directrix,
            start_reference,
            sweep_frames,
        )

        step = DEFAULT_MAX_ARC_STEP_DEG if max_arc_step_deg is None else max_arc_step_deg
        directrix = self.directrix
        t0 = sample_directrix(directrix.segments[0] if hasattr(directrix, "segments") else directrix, step)[1][0]
        up = self._sweep_up if self._sweep_up is not None else np.asarray(self.up, dtype=float)
        x0 = start_reference(t0, up=up, fallback=np.asarray(self.yvec, dtype=float))
        return sweep_frames(directrix, x0, step)

    def solid_geom(self) -> Geometry[FixedReferenceSweptAreaSolid]:
        from ada.api.beams import geom_beams as geo_conv

        return geo_conv.swept_beam_to_geom(self)

    def shell_geom(self) -> Geometry:
        return self.solid_geom()


def _as_sweep_path(curve):
    import ada.geom.curves as cu

    if isinstance(curve, (CurveOpen2d, CurveOpen3d, cu.IndexedPolyCurve, cu.Edge, cu.ArcLine, cu.PolyLine)):
        return curve
    if isinstance(curve, Iterable):
        return CurveOpen3d([tuple(float(c) for c in p) for p in curve])
    raise TypeError(f"BeamSweep: unsupported sweep path {type(curve).__name__}")


def _directrix_of(curve) -> CURVE_GEOM_TYPES:
    if isinstance(curve, CurveOpen2d):
        # The planar curve's points, first to last, with their corner radii. (A CurvePoly2d is a
        # closed polygon; the sweep runs its points as an open path, not round the polygon.)
        pts = [
            tuple(float(c) for c in p) + ((curve.radiis[i],) if i in curve.radiis else ())
            for i, p in enumerate(curve.points3d)
        ]
        return CurveOpen3d(pts).curve_geom()
    if isinstance(curve, CurveOpen3d):
        return curve.curve_geom()
    return curve
