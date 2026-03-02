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

def resolve_justification(beam: "Beam", just: Justification) -> Optional["Direction"]:
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


    #sign = 1.0 if just == Justification.FLUSH_TOP else -1.0
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

def get_offset_from_justification(beam: "Beam", just: Justification) -> Optional["Direction"]:
    """
    Backward-compatible name.
    Keep for now; later we can deprecate and rename everywhere to resolve_justification().
    """
    return resolve_justification(beam, just)


def get_justification(beam: Beam) -> Justification:
    """Justification line"""
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

        # --- your sign convention: local offsets start from -e ---
        off1_abs = -e1_abs
        off2_abs = -e2_abs

        # Convert absolute offsets -> LOCAL components (x,y,up)
        off1 = abs_vec_to_local_components(off1_abs)
        off2 = abs_vec_to_local_components(off2_abs)

        # --- geometric centroid adjustments (these are LOCAL y/z tweaks) ---
        p = self.beam.section.properties
        if getattr(p, "Cgy", None) is None or getattr(p, "Cgz", None) is None:
            raise ValueError(f"Section '{self.beam.section.name}' missing geometric centroid (Cgy/Cgz).")

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
            "end1": (float(off1[0]), float(off1[1]), float(off1[2])),
            "end2": (float(off2[0]), float(off2[1]), float(off2[2])),
            "avg": (float(avg[0]), float(avg[1]), float(avg[2])),
            "is_varying": bool(is_varying),
        }

    def get_cog(self) -> Point:
        """
        Beam COG in global coordinates.

        Conventions used here:
          - cog_line is midpoint of the beam line WITHOUT eccentricities.
          - Eccentricities e1/e2 are treated as offsets of the section/reference line
            relative to the beam line. If both ends exist and differ, we use their average
            for the COG (constant part).
          - Section geometric centroid uses Cgy/Cgz (not shear center).
          - For ANGULAR/TPROFILE we apply the same "flush-to-top" offset convention you use in Genie.
        """
        from ada import Point

        # Midpoint of beam line (no e)
        mid = self.get_cog_line()

        data = self.curve_offset_local()  # numeric offsets for COG
        ox, oy, oz = data["avg"]

        if data["is_varying"]:
            logger.warning(
                f"Beam '{self.beam.name}': e1 != e2. COG uses average curve offset.",
                RuntimeWarning,
                stacklevel=2,
            )

        x_abs, y_abs, up_abs = self._local_axes_in_absolute()
        offset_abs = ox * np.asarray(x_abs, float) + oy * np.asarray(y_abs, float) + oz * np.asarray(up_abs, float)
        cog_abs = mid + offset_abs

        return Point(cog_abs)

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
