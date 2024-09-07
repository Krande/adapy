from __future__ import annotations

from typing import TYPE_CHECKING

from ada.geom.surfaces import AdvancedFace

if TYPE_CHECKING:
    from ada.cadit.sat.store import SatStore


def create_advanced_face_from_sat(sat_object_data: str, sat_store: SatStore) -> AdvancedFace:
    """Creates an AdvancedFace from the SAT object data."""
    ref = sat_object_data.split()
    face_surface = None
    bounds = []
    same_sense = True

    spline_data = sat_store.get(ref[10])
    if face_surface is None:
        raise NotImplementedError("Only BSplineSurfaces are supported.")
    if len(bounds) < 1:
        raise NotImplementedError("No bounds found.")
    if len(bounds) < 2:
        raise NotImplementedError("Only one bound found.")

    return AdvancedFace(
        bounds=bounds,
        face_surface=face_surface,
        same_sense=same_sense,
    )
