from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING, NamedTuple, Optional

import numpy as np

from ada.core.vector_utils import is_identity_rot_matrix, vectors_are_close
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


def section_centroid_offset_local(section, strict: bool = True) -> np.ndarray:
    """Where the section's centroid sits relative to its profile origin, as local ``(x, y, z)``.

    Only ANGULAR and TPROFILE sections carry a correction: every other type is drawn with its origin
    on its centroid (an I, a box, a tube) or has no usable centroid (GENERAL, whose Cgy/Cgz are
    frequently undefined in Sesam GBEAMG records), so those return zero and demand nothing.

    - ANGULAR: the profile hangs below its origin (z in ``[-h, 0]``), so the centroid is ``Cgz - h``.
    - TPROFILE: the origin is mid-height, so the centroid is ``Cgz - h/2``.

    One of those two lacking ``Cgz`` (or ``h``) raises ValueError when ``strict``. Otherwise -- the
    FEM paths, which must still write a deck for a section read from a file that never said where its
    centroid is -- the section is taken as centred, and that is recorded in the conversion report as
    an approximation rather than left as a silent zero.
    """
    sec_type = section.type
    if sec_type not in (BaseTypes.ANGULAR, BaseTypes.TPROFILE):
        return np.zeros(3)

    p = section.properties
    h = float(section.h) if section.h is not None else None
    problem = None
    if getattr(p, "Cgz", None) is None:
        problem = f"missing geometric centroid (Cgz). section.properties: {p}"
    elif h is None:
        problem = "requires h to compute the centroid offset."
    if problem is not None:
        msg = f"Section '{section.name}', section type: {sec_type} {problem}"
        if strict:
            raise ValueError(msg)
        from ada.fem.formats import conversion_report

        conversion_report.current().approximated(
            "beam eccentricity",
            "Section",
            section.name,
            "no geometric centroid; the section is taken as centred on its profile origin when an offset is "
            "carried to the FEM",
            type=str(sec_type),
        )
        return np.zeros(3)
    cgz = float(p.Cgz)
    dz = cgz - h if sec_type == BaseTypes.ANGULAR else cgz - h / 2.0
    return np.array([0.0, 0.0, dz], dtype=float)


class CurveOffsetResult(NamedTuple):
    end1: tuple[float, float, float]
    end2: tuple[float, float, float]
    avg: tuple[float, float, float]
    is_varying: bool


class OffsetHelper:
    def __init__(self, beam: Beam):
        self.beam = beam

    def _local_axes_in_absolute(self, place_abs: "Placement" = None, ident_place: "Placement" = None):
        """
        Returns (xvec, yvec, up) expressed in the absolute/global system,
        respecting self.placement rotations (same logic as exporter).

        ``place_abs`` optionally supplies the beam's precomputed absolute
        placement so callers that already resolved it (the shared COG/length
        path) don't walk the ancestry again. ``ident_place`` likewise supplies
        the global reference frame, which is the same object for every beam.
        """
        from ada import Placement

        xvec = self.beam.xvec
        yvec = self.beam.yvec
        up = self.beam.up

        if self.beam.placement is not None:
            if ident_place is None:
                ident_place = Placement()
            if place_abs is None:
                place_abs = self.beam.placement.get_absolute_placement(include_rotations=True)

            # Only transform if rotation differs
            if not is_identity_rot_matrix(place_abs.rot_matrix):
                ori_vectors = place_abs.transform_array_from_other_place(
                    np.asarray([xvec, yvec, up]), ident_place, ignore_translation=True
                )
                xvec = ori_vectors[0]
                yvec = ori_vectors[1]
                up = ori_vectors[2]

        return xvec, yvec, up

    def _point_to_absolute(
        self, p: np.ndarray, place_abs: "Placement" = None, ident_place: "Placement" = None
    ) -> np.ndarray:
        """
        Transforms a point p from the beam's local system into absolute/global,
        using self.placement. If identity, returns p unchanged.

        ``place_abs`` and ``ident_place`` optionally supply the beam's
        precomputed absolute placement and the shared global reference frame
        (see _local_axes_in_absolute).
        """
        from ada import Placement

        if self.beam.placement is None:
            return p

        if ident_place is None:
            ident_place = Placement()
        if place_abs is None:
            place_abs = self.beam.placement.get_absolute_placement(include_rotations=True)

        # include translation
        return place_abs.transform_array_from_other_place(np.asarray([p]), ident_place, ignore_translation=False)[0]

    def curve_offset_local(self, axes=None, strict_centroid: bool = True) -> CurveOffsetResult:
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
        - ``strict_centroid=False`` takes an ANGULAR/TPROFILE section with no centroid as centred
          instead of raising (see :func:`section_centroid_offset_local`).
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
                    # An angle's profile hangs below its origin (z in [-h, 0]), so flush_top is the
                    # origin on the node line and flush_bottom is the origin h ABOVE it: e = -h * up,
                    # the same vector ``resolve_justification`` gives. This was +h * up, which put the
                    # profile 2h below the node and gave an HP220x10 -0.35543 where GeniE has +0.08457.
                    if just == Justification.FLUSH_TOP:
                        e_abs = zv_abs * 0.0
                    else:
                        e_abs = -zv_abs * float(sec0.h)

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

        # --- geometric centroid adjustment: where the centroid sits relative to the profile origin ---
        add_local = section_centroid_offset_local(self.beam.section, strict=strict_centroid)

        off1 = off1 + add_local
        off2 = off2 + add_local

        is_varying = not vectors_are_close(off1, off2)
        avg = 0.5 * (off1 + off2)

        return CurveOffsetResult(
            end1=(float(off1[0]), float(off1[1]), float(off1[2])),
            end2=(float(off2[0]), float(off2[1]), float(off2[2])),
            avg=(float(avg[0]), float(avg[1]), float(avg[2])),
            is_varying=bool(is_varying),
        )

    def has_offset_intent(self) -> bool:
        """Whether the beam says where it sits against its nodes: an explicit ``e1``/``e2``, or a
        flush alignment (``justification`` or the GeniE ``aligned_curve_offset`` it was read from).

        A beam that says neither has its centroid on the node line, which is what GeniE does and what
        the Sesam deck has always carried (no GECCEN).
        """
        if self.beam.e1 is not None or self.beam.e2 is not None:
            return True
        if self.beam.justification in (Justification.FLUSH_TOP, Justification.FLUSH_BOTTOM):
            return True
        md = getattr(self.beam, "metadata", None) or {}
        return md.get("aligned_curve_offset_alignment") in ("flush_top", "flush_bottom")

    def centroid_offset_local(self, axes=None) -> CurveOffsetResult | None:
        """The offset from each end node to the section **centroid**, in the beam's local frame --
        what a FEM eccentricity (Sesam GECCEN) holds. None when the beam states no offset
        (:meth:`has_offset_intent`).

        It is :meth:`curve_offset_local` -- ``-e`` plus the centroid's place against the profile
        origin -- taken in the beam's own, unrotated frame (``axes`` defaults to ``(xvec, yvec, up)``)
        so the caller can carry it to global coordinates with the beam's placement.
        """
        if not self.has_offset_intent():
            return None
        if axes is None:
            axes = (self.beam.xvec, self.beam.yvec, self.beam.up)
        return self.curve_offset_local(axes=axes, strict_centroid=False)

    def curve_offset_global(self) -> tuple[np.ndarray, np.ndarray]:
        """The FEM eccentricity of each end as a global vector: from the end node to the section centroid
        (:meth:`centroid_offset_local`), in the absolute system the mesh nodes are in (the placement applied).
        Zero for a beam that states no offset (:meth:`has_offset_intent`). What the mesher puts on the beam's
        elements (Sesam GECCEN) and the Code_Aster writer its offset nodes on."""
        from ada.api.transforms import to_global_vectors

        data = self.centroid_offset_local()
        if data is None:
            return np.zeros(3), np.zeros(3)
        xv, yv, up = (np.asarray(v, dtype=float) for v in (self.beam.xvec, self.beam.yvec, self.beam.up))

        def to_vector(comp) -> np.ndarray:
            return np.asarray(to_global_vectors(self.beam, comp[0] * xv + comp[1] * yv + comp[2] * up), dtype=float)

        return to_vector(data.end1), to_vector(data.end2)

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
        from ada import Placement

        place_abs = None
        ident_place = Placement()
        if self.beam.placement is not None:
            place_abs = self.beam.placement.get_absolute_placement(include_rotations=True)

        # Endpoints of the *beam line* in absolute/global coordinates
        p1_abs = np.asarray(
            self._point_to_absolute(self.beam.n1.p.copy(), place_abs=place_abs, ident_place=ident_place), dtype=float
        )
        p2_abs = np.asarray(
            self._point_to_absolute(self.beam.n2.p.copy(), place_abs=place_abs, ident_place=ident_place), dtype=float
        )

        # Local beam basis expressed in absolute/global coords (placement rotation applied)
        axes = self._local_axes_in_absolute(place_abs=place_abs, ident_place=ident_place)
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
