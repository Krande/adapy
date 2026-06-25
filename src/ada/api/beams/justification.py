from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING, NamedTuple, Optional

import numpy as np

from ada.sections.categories import BaseTypes

if TYPE_CHECKING:
    from ada import Beam, Direction, Placement, Point


class Justification(Enum):
    NA = "neutral axis"
    TOS = "top of steel"
    CUSTOM = "custom"
    UNSET = "unset"

    # New semantic values (explicit)
    FLUSH_TOP = "flush_top"
    FLUSH_BOTTOM = "flush_bottom"

    @staticmethod
    def from_str(label: str) -> "Justification":
        label = label.strip().lower()

        if label in ("na", "neutral axis"):
            return Justification.NA
        if label in ("tos", "top of steel"):
            return Justification.TOS
        if label in ("custom",):
            return Justification.CUSTOM
        if label in ("unset",):
            return Justification.UNSET

        # New explicit strings
        if label in ("flush_top", "flush top"):
            return Justification.FLUSH_TOP
        if label in ("flush_bottom", "flush bottom"):
            return Justification.FLUSH_BOTTOM

        raise ValueError(f"Unknown justification string: {label}")


def resolve_justification(beam: "Beam", just: Justification) -> Optional["Direction"]:
    from ada import Direction

    # ---- validation ----
    if not isinstance(just, Justification):
        raise ValueError(f"Unknown justification: {just}")

    if just == Justification.NA:
        return Direction(0, 0, 0)

    if just == Justification.TOS:
        if beam.section.h is None:
            return Direction(0, 0, 0)
        return beam.up * (beam.section.h / 2.0)

    if just in (Justification.CUSTOM, Justification.UNSET):
        return None

    sec = beam.section
    zv = beam.up

    sign = 1.0 if just == Justification.FLUSH_TOP else -1.0

    if sec.type in (BaseTypes.TUBULAR, BaseTypes.CIRCULAR):
        return sign * zv * float(sec.r)

    if sec.h is None:
        return Direction(0, 0, 0)

    if sec.type == BaseTypes.ANGULAR:
        if just == Justification.FLUSH_TOP:
            return Direction(0, 0, 0)
        return (-zv) * float(sec.h)

    if sec.type == BaseTypes.TPROFILE:
        return sign * zv * float(sec.h) / 2.0

    return sign * zv * float(sec.h) / 2.0


# todo this is the old method, remove when calls are updates
def get_offset_from_justification(beam: "Beam", just: Justification) -> Optional["Direction"]:
    """
    Backward-compatible name.
    Keep for now; later we can deprecate and rename everywhere to resolve_justification().
    """
    return resolve_justification(beam, just)


# todo remove and replace this function??
def get_justification(beam: Beam) -> Justification:
    """Justification line"""

    # todo instead use beam.justification
    #  the below tries to set justification bases on some logic, instead, the justification should be set when creating a beam?

    # Check if both self.e1 and self.e2 are None
    if beam.section.type in (beam.section.TYPES.TUBULAR, beam.section.TYPES.CIRCULAR):
        bm_height = beam.section.r * 2
    else:
        bm_height = beam.section.h

    if beam.e1 is None and beam.e2 is None:
        return Justification.NA
    elif beam.e1 is None or beam.e2 is None:
        return Justification.CUSTOM
    elif beam.e1.is_equal(beam.e2) and beam.e1.is_equal(beam.up * bm_height / 2):
        return Justification.TOS
    else:
        return Justification.CUSTOM


class CurveOffsetResult(NamedTuple):
    end1: tuple[float, float, float]
    end2: tuple[float, float, float]
    avg: tuple[float, float, float]
    is_varying: bool


class OffsetHelper:
    def __init__(self, beam: Beam):
        self.beam = beam

    def _local_axes_in_absolute(self, place_abs: "Placement" = None):
        """
        Returns (xvec, yvec, up) expressed in the absolute/global system,
        respecting self.placement rotations (same logic as exporter).

        ``place_abs`` optionally supplies the beam's precomputed absolute
        placement so callers that already resolved it (the shared COG/length
        path) don't walk the ancestry again.
        """
        from ada import Placement

        xvec = self.beam.xvec
        yvec = self.beam.yvec
        up = self.beam.up

        if self.beam.placement is not None:
            ident_place = Placement()
            if place_abs is None:
                place_abs = self.beam.placement.get_absolute_placement(include_rotations=True)

            # Only transform if rotation differs
            if not np.allclose(place_abs.rot_matrix, ident_place.rot_matrix):
                ori_vectors = place_abs.transform_array_from_other_place(
                    np.asarray([xvec, yvec, up]), ident_place, ignore_translation=True
                )
                xvec = ori_vectors[0]
                yvec = ori_vectors[1]
                up = ori_vectors[2]

        return xvec, yvec, up

    def _point_to_absolute(self, p: np.ndarray, place_abs: "Placement" = None) -> np.ndarray:
        """
        Transforms a point p from the beam's local system into absolute/global,
        using self.placement. If identity, returns p unchanged.

        ``place_abs`` optionally supplies the beam's precomputed absolute
        placement (see _local_axes_in_absolute).
        """
        from ada import Placement

        if self.beam.placement is None:
            return p

        ident_place = Placement()
        if place_abs is None:
            place_abs = self.beam.placement.get_absolute_placement(include_rotations=True)

        # include translation
        return place_abs.transform_array_from_other_place(np.asarray([p]), ident_place, ignore_translation=False)[0]

    def curve_offset_local(self, axes=None) -> CurveOffsetResult:
        """
        Compute local (x,y,z) curve offsets for Genie / COG, at end1 and end2.

        Returns a CurveOffsetResult object with:
          - end1: (ox1, oy1, oz1)
          - end2: (ox2, oy2, oz2)
          - avg:  (ox,  oy,  oz)
          - is_varying: bool

        Notes:
        - Uses geometric centroid Cgy/Cgz.
        - Uses sign convention: local offsets start from -e.
        - ``axes`` optionally supplies the precomputed (x,y,up) absolute basis
          from _local_axes_in_absolute so the shared COG/length path doesn't
          recompute it here.
        """

        # Absolute axes for the beam's local basis (x, y, up) expressed in global coords
        if axes is None:
            axes = self._local_axes_in_absolute()
        x_abs, y_abs, up_abs = axes
        x_abs = np.asarray(x_abs, dtype=float)
        y_abs = np.asarray(y_abs, dtype=float)
        up_abs = np.asarray(up_abs, dtype=float)

        def abs_vec_to_local_components(v_abs: np.ndarray) -> np.ndarray:
            """Project an absolute/global vector onto (x,y,up) -> local components."""
            return np.array(
                [float(np.dot(v_abs, x_abs)), float(np.dot(v_abs, y_abs)), float(np.dot(v_abs, up_abs))],
                dtype=float,
            )

        # --- e1/e2 as absolute vectors ---
        e1_abs = np.array([*self.beam.e1], dtype=float) if self.beam.e1 is not None else np.zeros(3)
        e2_abs = np.array([*self.beam.e2], dtype=float) if self.beam.e2 is not None else np.zeros(3)

        # ------------------------------------------------------------------
        # Fallback: derive numeric e from justification intent (if no e1/e2)
        # ------------------------------------------------------------------
        if self.beam.e1 is None and self.beam.e2 is None:

            just = self.beam.justification
            sec0 = self.beam.section
            zv_abs = np.asarray(self.beam.up, dtype=float)  # beam.up is "top" in absolute coords

            # If imported from Genie, prefer explicit aligned metadata
            alignment_str = None
            if getattr(self.beam, "metadata", None):
                alignment_str = self.beam.metadata.get("aligned_curve_offset_alignment")

            if alignment_str == "flush_top":
                just = Justification.FLUSH_TOP
            elif alignment_str == "flush_bottom":
                just = Justification.FLUSH_BOTTOM

            if just in (Justification.FLUSH_TOP, Justification.FLUSH_BOTTOM):
                flush_factor = 1.0 if just == Justification.FLUSH_TOP else -1.0

                # --- section-specific rules (match Genie semantics) ---
                if sec0.type == sec0.TYPES.ANGULAR:
                    if just == Justification.FLUSH_TOP:
                        e_abs = zv_abs * 0.0
                    else:
                        e_abs = zv_abs * float(sec0.h)

                elif sec0.type == sec0.TYPES.TUBULAR:
                    e_abs = flush_factor * zv_abs * float(sec0.r)

                else:
                    e_abs = flush_factor * zv_abs * (float(sec0.h) / 2.0)

                e1_abs = np.array(e_abs, dtype=float)
                e2_abs = np.array(e_abs, dtype=float)
            # todo below elifs not tested yet
            elif just in (Justification.NA, Justification.UNSET):
                e_abs = zv_abs * 0.0
                e1_abs = np.array(e_abs, dtype=float)
                e2_abs = np.array(e_abs, dtype=float)
            elif just == Justification.TOS:
                if self.beam.section.h is None:
                    e_abs = zv_abs * 0.0
                    e1_abs = np.array(e_abs, dtype=float)
                    e2_abs = np.array(e_abs, dtype=float)
                else:
                    e_abs = zv_abs * (-self.beam.section.h / 2.0)
                    e1_abs = np.array(e_abs, dtype=float)
                    e2_abs = np.array(e_abs, dtype=float)
            elif just in (Justification.CUSTOM):
                e1_abs = np.array(zv_abs * (-self.beam.e1), dtype=float)
                e2_abs = np.array(zv_abs * (-self.beam.e2), dtype=float)
            else:
                raise ValueError(f"Unknown justification: {just}")

        # --- your sign convention: local offsets start from -e ---
        off1_abs = -e1_abs
        off2_abs = -e2_abs

        # Convert absolute offsets -> LOCAL components (x,y,up)
        off1 = abs_vec_to_local_components(off1_abs)
        off2 = abs_vec_to_local_components(off2_abs)

        # --- geometric centroid adjustments (these are LOCAL y/z tweaks) ---
        # Only ANGULAR / TPROFILE actually consume the geometric centroid; every
        # other section type (incl. GENERAL, whose Cgy/Cgz are frequently
        # undefined in Sesam GBEAMG records) gets no centroid tweak, so don't
        # demand a centroid it never uses.
        p = self.beam.section.properties
        sec_type = self.beam.section.type
        h = float(self.beam.section.h) if self.beam.section.h is not None else None

        if sec_type in (BaseTypes.ANGULAR, BaseTypes.TPROFILE):
            if getattr(p, "Cgz", None) is None:
                raise ValueError(
                    f"Section '{self.beam.section.name}', section type: {sec_type} missing "
                    f"geometric centroid (Cgz). section.properties: {p}"
                )
            cgz = float(p.Cgz)
            if sec_type == BaseTypes.ANGULAR:
                if h is None:
                    raise ValueError("ANGULAR requires h to compute flush offset.")
                dz = cgz - h
            else:  # TPROFILE
                if h is None:
                    raise ValueError("TPROFILE requires h to compute offset.")
                dz = cgz - h / 2.0
            dy = 0.0
        else:
            dz = 0.0
            dy = 0.0

        add_local = np.array([0.0, dy, dz], dtype=float)

        off1 = off1 + add_local
        off2 = off2 + add_local

        is_varying = not np.allclose(off1, off2)
        avg = 0.5 * (off1 + off2)

        return CurveOffsetResult(
            end1=(float(off1[0]), float(off1[1]), float(off1[2])),
            end2=(float(off2[0]), float(off2[1]), float(off2[2])),
            avg=(float(avg[0]), float(avg[1]), float(avg[2])),
            is_varying=bool(is_varying),
        )

    def _offset_endpoints_abs(self) -> tuple[np.ndarray, np.ndarray]:
        """Absolute, offset-adjusted beam endpoints ``(start, end)``.

        The endpoints are the beam-line nodes transformed to global coords and
        shifted by the end1/end2 curve offsets:

            start = p1_abs + off1_abs
            end   = p2_abs + off2_abs

        get_cog (their midpoint) and get_effective_length (their distance) are
        both derived from these, so sharing this resolves the curve offsets,
        the absolute basis and the endpoint transforms only once per beam. The
        beam's absolute placement is resolved a single time here and threaded
        into the helpers rather than re-walking the ancestry in each.
        """
        place_abs = None
        if self.beam.placement is not None:
            place_abs = self.beam.placement.get_absolute_placement(include_rotations=True)

        # Endpoints of the *beam line* in absolute/global coordinates
        p1_abs = np.asarray(self._point_to_absolute(self.beam.n1.p.copy(), place_abs=place_abs), dtype=float)
        p2_abs = np.asarray(self._point_to_absolute(self.beam.n2.p.copy(), place_abs=place_abs), dtype=float)

        # Local beam basis expressed in absolute/global coords (placement rotation applied)
        axes = self._local_axes_in_absolute(place_abs=place_abs)
        x_abs = np.asarray(axes[0], dtype=float)
        y_abs = np.asarray(axes[1], dtype=float)
        up_abs = np.asarray(axes[2], dtype=float)

        # Curve offsets at BOTH ends (local components in beam basis)
        data = self.curve_offset_local(axes=axes)
        ox1, oy1, oz1 = data.end1
        ox2, oy2, oz2 = data.end2

        off1_abs = float(ox1) * x_abs + float(oy1) * y_abs + float(oz1) * up_abs
        off2_abs = float(ox2) * x_abs + float(oy2) * y_abs + float(oz2) * up_abs

        return p1_abs + off1_abs, p2_abs + off2_abs

    def get_cog(self) -> "Point":
        """
        Beam COG in global coordinates: the midpoint of the offset-adjusted
        absolute endpoints. Correctly handles per-end axial/transverse offsets
        and placement rotation/translation (see _offset_endpoints_abs).
        """
        from ada import Point

        start_abs, end_abs = self._offset_endpoints_abs()
        return Point(0.5 * (start_abs + end_abs))

    def get_effective_length(self) -> float:
        """
        Beam length after curve offsets (including axial components).
        """
        start_abs, end_abs = self._offset_endpoints_abs()
        return float(np.linalg.norm(end_abs - start_abs))

    def get_cog_and_length(self) -> tuple["Point", float]:
        """COG (endpoint midpoint) and effective length from a single offset
        solve. Part.calculate_cog needs both per beam; computing them together
        halves the curve-offset / placement / transform work versus calling
        get_cog and get_effective_length separately.
        """
        from ada import Point

        start_abs, end_abs = self._offset_endpoints_abs()
        cog = Point(0.5 * (start_abs + end_abs))
        length = float(np.linalg.norm(end_abs - start_abs))
        return cog, length

    def get_cog_line(self) -> Point:
        """
        Midpoint of the beam line between n1 and n2 ONLY (no eccentricities).
        Returned in absolute/global coordinates (placement applied).
        """
        from ada import Point

        p1 = self.beam.n1.p.copy()
        p2 = self.beam.n2.p.copy()
        mid = 0.5 * (p1 + p2)

        mid_abs = self._point_to_absolute(mid)

        return Point(mid_abs)
