from __future__ import annotations

from ada.cadit.sat.read.bsplinesurface import create_bsplinesurface_from_sat
from ada.geom.surfaces import AdvancedFace
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ada.cadit.sat.store import SatStore


def create_advanced_face_from_sat(sat_object_data: str, sat_store: SatStore) -> AdvancedFace:
    """Creates an AdvancedFace from the SAT object data."""
    ref = sat_object_data.split()
    spline_data = sat_store.get(ref[10])
    b_spline_surf = create_bsplinesurface_from_sat(' '.join(spline_data))
    return AdvancedFace()
