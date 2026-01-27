from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING

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
    FLUSH_OFFSET = "FLUSH_OFFSET"

    @staticmethod
    def from_str(label: str) -> Justification:
        label = label.lower()
        if label in ("na", "neutral axis"):
            return Justification.NA
        elif label in ("tos", "top of steel"):
            return Justification.TOS
        elif label in ("custom",):
            return Justification.CUSTOM
        elif label in ("unset",):
            return Justification.UNSET
        elif label in ("flush offset",):
            return Justification.FLUSH_OFFSET
        else:
            raise ValueError(f"Unknown justification string: {label}")


def get_offset_from_justification(beam: Beam, just: Justification) -> Direction | None:
    from ada import Direction

    if just == Justification.NA:
        return Direction(0, 0, 0)
    elif just == Justification.TOS:
        return beam.up * beam.section.h / 2
    elif just == Justification.CUSTOM:
        return None
    else:
        raise ValueError(f"Unknown justification: {just}")


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

        if self.beam.placement is not None and self.beam.placement.is_identity() is False:
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

        if self.beam.placement is None or self.beam.placement.is_identity():
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
            "avg":  (ox,  oy,  oz),   # average of end1/end2 (useful for COG)
            "is_varying": bool,       # True if end1 != end2
          }

        Notes:
        - Uses geometric centroid Cgy/Cgz.
        - Uses your sign convention: local offsets start from -e.
        """
        # --- e1/e2 -> numeric vectors ---
        e1 = np.array([*self.beam.e1], dtype=float) if self.beam.e1 is not None else np.zeros(3)
        e2 = np.array([*self.beam.e2], dtype=float) if self.beam.e2 is not None else np.zeros(3)

        # your sign convention
        off1 = -e1
        off2 = -e2

        # --- section geometric centroid data ---
        p = self.beam.section.properties
        if getattr(p, "Cgy", None) is None or getattr(p, "Cgz", None) is None:
            raise ValueError(f"Section '{self.beam.section.name}' missing geometric centroid (Cgy/Cgz).")

        cgz = float(p.Cgz)
        h = float(self.beam.section.h) if self.beam.section.h is not None else None

        # Numeric offsets: place section relative to beam curve explicitly
        # Default: place curve at centroid (add centroid coords)
        # Special: your existing conventions for ANGULAR/TPROFILE
        if self.beam.section.type == BaseTypes.ANGULAR:
            if h is None:
                raise ValueError("ANGULAR requires h to compute flush offset.")
            # flush-to-top: dz = (Cgz - h) = -ez
            dz = cgz - h
            dy = 0
        elif self.beam.section.type == BaseTypes.TPROFILE:
            if h is None:
                raise ValueError("TPROFILE requires h to compute offset.")
            dz = cgz - h / 2.0
            dy = 0  # should be 0 for symmetrical profiles!
        # elif self.section.type == BaseTypes.IPROFILE and self.section.w_btn != self.section.w_top:
        #    logger.warning(f"IPROFILE with w_btn != w_top not yet supported. Using default offset.")
        #    dz = 0
        #    dy = 0
        else:
            dz = 0
            dy = 0

        add = np.array([0.0, dy, dz], dtype=float)
        off1 = off1 + add
        off2 = off2 + add

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
