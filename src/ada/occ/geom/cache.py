from __future__ import annotations

from typing import TYPE_CHECKING, Dict

from OCC.Core.TopoDS import TopoDS_Solid

from ada.occ.geom import geom_to_occ_geom

if TYPE_CHECKING:
    from ada import Beam, Plate, PrimBox

# — use normal dicts so objects stay alive —
occ_solid_cache: Dict[str, TopoDS_Solid] = {}
occ_shell_cache: Dict[str, TopoDS_Solid] = {}


def get_solid_occ(occ_object: Plate | Beam | PrimBox) -> TopoDS_Solid:
    """
    Return (and cache) the OCC solid for this plate or beam.
    Uses a plain dict, so the solid will stay cached until you restart.
    """
    key = occ_object.guid
    if key not in occ_solid_cache:
        occ_solid_cache[key] = geom_to_occ_geom(occ_object.solid_geom())
    return occ_solid_cache[key]


def get_shell_occ(occ_object: Plate | Beam) -> TopoDS_Solid:
    """
    Same for shell geometry.
    """
    key = occ_object.guid
    if key not in occ_shell_cache:
        occ_shell_cache[key] = geom_to_occ_geom(occ_object.shell_geom())
    return occ_shell_cache[key]
