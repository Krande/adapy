from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from ada.base.physical_objects import BackendGeom
from ada.base.types import GeomRepr
from ada.occ.tessellating import tessellate_shape
from ada.visit.concept import ObjectMesh
from ada.visit.config import ExportConfig

if TYPE_CHECKING:
    pass


def occ_geom_to_poly_mesh(
    obj: BackendGeom,
    export_config: ExportConfig = ExportConfig(),
    opt_func: Callable = None,
    geom_repr: GeomRepr = GeomRepr.SOLID,
) -> ObjectMesh:
    if geom_repr == GeomRepr.SOLID:
        geom = obj.solid_occ()
    elif geom_repr == GeomRepr.SHELL:
        geom = obj.shell_occ()
    else:
        export_config.render_edges = True
        geom = obj.line_occ()

    tm = tessellate_shape(
        geom,
        export_config.quality,
        export_config.render_edges,
        export_config.parallel,
    )
    colour = list(obj.color)
    return ObjectMesh(obj.guid, tm.faces, tm.positions, tm.normals, colour, translation=export_config.volume_center)
