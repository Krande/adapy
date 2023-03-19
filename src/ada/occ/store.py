from __future__ import annotations

import pathlib
from typing import TYPE_CHECKING

from OCC.Extend.DataExchange import read_step_file

import ada
from ada.base.types import GeomRepr
from ada.config import get_logger

if TYPE_CHECKING:
    from OCC.Core.TopoDS import TopoDS_Shape

    from ada.base.physical_objects import BackendGeom
    from ada.occ.step.writer import StepWriter

logger = get_logger()


class OCCStore:
    def __init__(self, step_file: str | pathlib.Path = None):
        self.step_file = step_file

    def iter_shapes(self):
        for shp in read_step_file(self.step_file, as_compound=False):
            yield shp

    @staticmethod
    def get_writer() -> StepWriter:
        from ada.occ.step.writer import StepWriter

        return StepWriter("AdaStep")

    @staticmethod
    def shape_iterator(
        part: ada.Part | BackendGeom, geom_repr: GeomRepr = GeomRepr.SOLID
    ) -> tuple[BackendGeom, TopoDS_Shape]:
        if isinstance(geom_repr, str):
            geom_repr = GeomRepr.from_str(geom_repr)

        def safe_geom(obj_):
            try:
                if geom_repr == GeomRepr.SOLID:
                    return obj_.solid()
                elif geom_repr == GeomRepr.SHELL:
                    return obj_.shell()
            except RuntimeError as e:
                logger.warning(f"Failed to add shape {obj.name} due to {e}")
                return None

        if isinstance(part, (ada.Part, ada.Assembly)):
            for obj in part.get_all_physical_objects(pipe_to_segments=True):
                if isinstance(geom_repr, str):
                    geom_repr = GeomRepr.from_str(geom_repr)

                if issubclass(type(obj), ada.Shape):
                    geom = safe_geom(obj)
                elif isinstance(obj, (ada.Beam, ada.Plate, ada.Wall)):
                    geom = safe_geom(obj)
                elif isinstance(obj, (ada.PipeSegStraight, ada.PipeSegElbow)):
                    geom = safe_geom(obj)
                else:
                    logger.error(f"Geometry type {type(obj)} not yet implemented")
                    geom = None

                if geom is None:
                    continue

                yield obj, geom

        else:
            yield part, safe_geom(part)
