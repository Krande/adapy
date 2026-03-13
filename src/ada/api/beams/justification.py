from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING, Optional

import numpy as np

from ada.config import logger
from ada.sections.categories import BaseTypes

if TYPE_CHECKING:
    from ada import Beam, Direction, Point


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


# todo remove?
def OLDresolve_justification(beam: "Beam", just: Justification) -> Optional["Direction"]:
    """
    Resolve a semantic justification into a *local-z based* offset vector (in GLOBAL coords),
    using beam.up as the "top" direction (same conceptual meaning as Genie).

    Returns:
      - Direction(...) for NA/TOS/FLUSH_* when computable
      - None for CUSTOM/UNSET (meaning: must rely on explicit e1/e2)
    """
    from ada import Direction

    if just == Justification.NA:
        return Direction(0, 0, 0)

    if just == Justification.TOS:
        # Keep your existing convention here (you can later decide if TOS should mean +h/2 or something else)
        if beam.section.h is None:
            return Direction(0, 0, 0)
        return beam.up * (beam.section.h / 2.0)

    if just in (Justification.CUSTOM, Justification.UNSET):
        return None

    # Flush semantics:
    # - "top" is +beam.up
    # - "bottom" is -beam.up
    # Offset magnitude depends on section type
    sec = beam.section
    zv = beam.up

    # sign = 1.0 if just == Justification.FLUSH_TOP else -1.0
    sign = 1.0

    # NOTE:
    # This is the *semantic* resolver. It must match Genie’s understanding.
    # We keep it intentionally simple: “flush” means move by half depth (or radius)
    # except where Genie differs.
    if sec.type in (BaseTypes.TUBULAR, BaseTypes.CIRCULAR):
        return sign * zv * float(sec.r)

    if sec.h is None:
        return Direction(0, 0, 0)

    # Genie special cases you already identified:
    if sec.type == BaseTypes.ANGULAR:
        # In your current Genie exporter: ANGULAR flush_top is effectively "no extra offset" (0)
        # (because angular origin is already at the flush reference in Genie’s aligned_offset behavior)
        if just == Justification.FLUSH_TOP:
            return Direction(0, 0, 0)
        # flush_bottom = move down by full height
        return (-zv) * float(sec.h)

    if sec.type == BaseTypes.TPROFILE:
        # Genie legacy mapping already flips to flush_bottom for TPROFILE.
        # Here: flush_top means +h/2, flush_bottom means -h/2 (relative to beam.up)
        return sign * zv * float(sec.h) / 2.0

    # Default for most profiles (I/BOX/CHANNEL/FLATBAR/etc.)
    return sign * zv * float(sec.h) / 2.0


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


class OffsetHelper:
    def __init__(self, beam: Beam):
        self.beam = beam

    def _local_axes_in_absolute(self):
        """
        Returns (xvec, yvec, up) expressed in the absolute/global system,
        respecting self.placement rotations (same logic as exporter).
        """
        from ada import Placement

        xvec = self.beam.xvec
        yvec = self.beam.yvec
        up = self.beam.up

        if self.beam.placement is not None:
            ident_place = Placement()
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

    def _point_to_absolute(self, p: np.ndarray) -> np.ndarray:
        """
        Transforms a point p from the beam's local system into absolute/global,
        using self.placement. If identity, returns p unchanged.
        """
        from ada import Placement

        if self.beam.placement is None:
            return p

        ident_place = Placement()
        place_abs = self.beam.placement.get_absolute_placement(include_rotations=True)

        # include translation
        return place_abs.transform_array_from_other_place(np.asarray([p]), ident_place, ignore_translation=False)[0]

    # todo this is the new method for where the offsets are calculated, and should replace get_offset_from_justification and resolve_justification ?
    #  needs to be updated for TOS and other
    def curve_offset_local(self):
        """
        Compute local (x,y,z) curve offsets for Genie / COG, at end1 and end2.

        Returns a dict:
          {
            "end1": (ox1, oy1, oz1),
            "end2": (ox2, oy2, oz2),
            "avg":  (ox,  oy,  oz),
            "is_varying": bool,
          }

        Notes:
        - Uses geometric centroid Cgy/Cgz.
        - Uses sign convention: local offsets start from -e.
        """

        # Absolute axes for the beam's local basis (x, y, up) expressed in global coords
        x_abs, y_abs, up_abs = self._local_axes_in_absolute()
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
        p = self.beam.section.properties
        if getattr(p, "Cgy", None) is None or getattr(p, "Cgz", None) is None:
            raise ValueError(
                f"Section '{self.beam.section.name}', section type: {self.beam.section.type} missing geometric centroid (Cgy/Cgz). section.properties: {p}"
            )

        cgz = float(p.Cgz)
        h = float(self.beam.section.h) if self.beam.section.h is not None else None

        if self.beam.section.type == BaseTypes.ANGULAR:
            if h is None:
                raise ValueError("ANGULAR requires h to compute flush offset.")
            dz = cgz - h
            dy = 0.0

        elif self.beam.section.type == BaseTypes.TPROFILE:
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

        return {
            "end1": (float(off1[0]), float(off1[1]), float(off1[2])),  # todo return as Direction instead?
            "end2": (float(off2[0]), float(off2[1]), float(off2[2])),  # todo return as Direction instead?
            "avg": (float(avg[0]), float(avg[1]), float(avg[2])),  # todo return as Direction instead?
            "is_varying": bool(is_varying),
        }

    def get_cog(self) -> "Point":
        """
        Beam COG in global coordinates, accounting for end-specific offsets.

        This computes the centroid of a straight prismatic beam whose reference line endpoints
        are shifted by curve offsets at end1 and end2:

            start_abs = p1_abs + off1_abs
            end_abs   = p2_abs + off2_abs
            cog_abs   = 0.5 * (start_abs + end_abs)

        This correctly handles:
          - different axial (local x) offsets at each end (beam appears longer/shorter)
          - varying y/z offsets (centroid uses the average via endpoint midpoint)
          - placement rotation/translation (via _local_axes_in_absolute and _point_to_absolute)
        """
        from ada import Point

        # Endpoints of the *beam line* in absolute/global coordinates (placement applied)
        p1_abs = self._point_to_absolute(self.beam.n1.p.copy())
        p2_abs = self._point_to_absolute(self.beam.n2.p.copy())

        # Get curve offsets at BOTH ends (local components in beam basis)
        data = self.curve_offset_local()
        ox1, oy1, oz1 = data["end1"]
        ox2, oy2, oz2 = data["end2"]

        # Local beam basis expressed in absolute/global coords (placement rotation applied)
        x_abs, y_abs, up_abs = self._local_axes_in_absolute()
        x_abs = np.asarray(x_abs, dtype=float)
        y_abs = np.asarray(y_abs, dtype=float)
        up_abs = np.asarray(up_abs, dtype=float)

        # Convert local offset components -> absolute vectors
        off1_abs = float(ox1) * x_abs + float(oy1) * y_abs + float(oz1) * up_abs
        off2_abs = float(ox2) * x_abs + float(oy2) * y_abs + float(oz2) * up_abs

        # Offset endpoints and midpoint
        start_abs = np.asarray(p1_abs, dtype=float) + off1_abs
        end_abs = np.asarray(p2_abs, dtype=float) + off2_abs
        cog_abs = 0.5 * (start_abs + end_abs)

        # Optional: warn when varying offsets exist (kept from your earlier intent)
        if data.get("is_varying", False):
            logger.warning(
                "Beam '%s': curve offset varies between ends; COG computed from offset endpoints.",
                self.beam.name,
            )

        logger.warning(
            "Beam '%s': varying curve offsets detected end1=%s end2=%s. COG computed from offset endpoints.",
            self.beam.name,
            data["end1"],
            data["end2"],
        )

        return Point(cog_abs)

    def get_effective_length(self) -> float:
        """
        Beam length after curve offsets (including axial components).
        """
        p1 = self._point_to_absolute(self.beam.n1.p.copy())
        p2 = self._point_to_absolute(self.beam.n2.p.copy())

        data = self.curve_offset_local()

        ox1, oy1, oz1 = data["end1"]
        ox2, oy2, oz2 = data["end2"]

        x_abs, y_abs, up_abs = self._local_axes_in_absolute()

        x_abs = np.asarray(x_abs, float)
        y_abs = np.asarray(y_abs, float)
        up_abs = np.asarray(up_abs, float)

        off1 = ox1 * x_abs + oy1 * y_abs + oz1 * up_abs
        off2 = ox2 * x_abs + oy2 * y_abs + oz2 * up_abs

        p1 = np.asarray(p1, float) + off1
        p2 = np.asarray(p2, float) + off2

        return float(np.linalg.norm(p2 - p1))

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
