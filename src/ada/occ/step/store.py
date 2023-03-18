from __future__ import annotations

import pathlib
from typing import TYPE_CHECKING

from OCC.Extend.DataExchange import read_step_file

import ada
from ada.base.types import GeomRepr

if TYPE_CHECKING:
    from OCC.Core.TopoDS import TopoDS_Shape

    from ada.base.physical_objects import BackendGeom


class StepStore:
    def __init__(self, step_file: str | pathlib.Path = None):
        self.step_file = step_file

    def iter_shapes(self):
        for shp in read_step_file(self.step_file, as_compound=False):
            yield shp

    @staticmethod
    def get_writer():
        from .writer import StepWriter

        return StepWriter()

    @staticmethod
    def shape_iterator(part: ada.Part, geom_repr: GeomRepr = None) -> tuple[BackendGeom, TopoDS_Shape]:
        if isinstance(geom_repr, str):
            geom_repr = GeomRepr.from_str(geom_repr)

        def safe_geom(obj_):
            try:
                if geom_repr == GeomRepr.SOLID:
                    return obj_.solid()
                elif geom_repr == GeomRepr.SHELL:
                    return obj_.shell()
            except RuntimeError as e:
                print("Failed to add shape", obj.name, e)
                return None

        for obj in part.get_all_physical_objects(pipe_to_segments=True):
            if isinstance(geom_repr, str):
                geom_repr = GeomRepr.from_str(geom_repr)

            if issubclass(type(obj), ada.Shape):
                yield obj, safe_geom(obj)
            elif isinstance(obj, (ada.Beam, ada.Plate, ada.Wall)):
                yield obj, safe_geom(obj)
            elif isinstance(obj, (ada.PipeSegStraight, ada.PipeSegElbow)):
                yield obj, safe_geom(obj)
            else:
                raise NotImplementedError(f"Geometry type {type(obj)} not yet implemented")
