from __future__ import annotations

import traceback
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Iterable

import ada
from ada import Units
from ada.base.types import GeomRepr
from ada.config import Config, logger
from ada.occ.exceptions import (
    UnableToCreateSolidOCCGeom,
    UnableToCreateSurfaceOCCGeom,
    UnableToTransformOCCShape,
)
from ada.visit.colors import Color

if TYPE_CHECKING:
    from OCC.Core.TopoDS import TopoDS_Shape

    from ada.base.physical_objects import BackendGeom
    from ada.cadit.step.store import StepStore
    from ada.cadit.step.write.writer import StepWriter


class OCCStore:
    @staticmethod
    def get_step_writer() -> StepWriter:
        from ada.cadit.step.write.writer import StepWriter

        return StepWriter("AdaStep")

    @staticmethod
    def get_reader(step_filepath) -> StepStore:
        from ada.cadit.step.store import StepStore

        return StepStore(step_filepath)

    @staticmethod
    def shape_iterator(
        part: ada.Part | BackendGeom | StepStore,
        geom_repr: GeomRepr = GeomRepr.SOLID,
        render_override: dict[str, GeomRepr] = None,
    ) -> tuple[BackendGeom, TopoDS_Shape]:
        from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_Transform
        from OCC.Core.gp import gp_Trsf, gp_Vec

        from ada.cadit.step.store import StepStore

        if render_override is None:
            render_override = {}

        if isinstance(geom_repr, str):
            geom_repr = GeomRepr.from_str(geom_repr)

        def safe_geom(obj_, name_ref=None):
            geo_repr = render_override.get(obj_.guid, geom_repr)
            if geo_repr == GeomRepr.SOLID:
                try:
                    occ_geom = obj_.solid_occ()
                except (RuntimeError, BaseException) as e:
                    exc = traceback.format_exc()
                    err_msg = f'Failed to add shape {obj.name} due to "{e}" from {name_ref} in {exc}'
                    if Config().general_occ_silent_fail:
                        logger.warning(err_msg)
                        return None
                    raise UnableToCreateSolidOCCGeom(err_msg)
            elif geo_repr == GeomRepr.SHELL:
                try:
                    occ_geom = obj_.shell_occ()
                except (RuntimeError, BaseException) as e:
                    exc = traceback.format_exc()
                    err_msg = f"Failed to create shell geometry for {obj.name} due to {e} from {name_ref} in {exc}"
                    if Config().general_occ_silent_fail:
                        logger.warning(err_msg)
                        return None
                    raise UnableToCreateSurfaceOCCGeom(err_msg)
            else:
                raise ValueError(f"Invalid geometry representation {geo_repr}")

            position = obj.parent.placement.to_axis2placement3d(use_absolute_placement=True)

            try:
                trsf = gp_Trsf()
                trsf.SetTranslation(gp_Vec(*position.location))
                occ_geom = BRepBuilderAPI_Transform(occ_geom, trsf, True).Shape()
            except (RuntimeError, BaseException) as e:
                exc = traceback.format_exc()
                err_msg = f"Failed to transform geometry for {obj.name} due to {e} from {name_ref} in {exc}"
                if Config().general_occ_silent_fail:
                    logger.warning(err_msg)
                    return None
                raise UnableToTransformOCCShape(err_msg)

            return occ_geom

        if isinstance(part, StepStore):
            for shape in part.iter_all_shapes(include_colors=True):
                yield shape

        if isinstance(part, (ada.Part, ada.Assembly)):
            for obj in part.get_all_physical_objects(pipe_to_segments=True):
                if isinstance(geom_repr, str):
                    geom_repr = GeomRepr.from_str(geom_repr)

                if issubclass(type(obj), ada.Shape):
                    geom = safe_geom(obj, part.name)
                elif isinstance(obj, (ada.Beam, ada.Plate, ada.PlateCurved, ada.Wall)):
                    geom = safe_geom(obj, part.name)
                elif isinstance(obj, (ada.PipeSegStraight, ada.PipeSegElbow)):
                    geom = safe_geom(obj, part.name)
                else:
                    logger.error(f"Geometry type {type(obj)} not yet implemented")
                    geom = None

                if geom is None:
                    continue

                yield obj, geom

        else:
            yield part, safe_geom(part)

    @staticmethod
    def to_gltf(
        gltf_file_path,
        occ_shape_iterable: Iterable[OccShape],
        line_defl: float = None,
        angle_def: float = None,
        export_units: Units | str = Units.M,
        progress_callback: Callable[[int, int], None] = None,
        source_units: Units | str = Units.M,
    ):
        from .gltf_writer import to_gltf

        to_gltf(
            gltf_file_path,
            occ_shape_iterable,
            line_defl,
            angle_def,
            export_units,
            progress_callback,
            source_units=source_units,
        )


@dataclass
class OccShape:
    shape: TopoDS_Shape
    color: Color | None = None
    num_tot_entities: int = 0
    name: str | None = None
