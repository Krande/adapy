import pathlib
from typing import Callable

import ifcopenshell.geom
import ifcopenshell.validate
from ifcopenshell import ifcopenshell_wrapper

import ada
from ada.base.units import Units
from ada.cadit.ifc.utils import add_colour, create_local_placement
from ada.cadit.ifc.write.write_ifc import IfcWriter
from ada.cadit.step.store import StepStore
from ada.config import logger
from ada.core.guid import create_guid
from ada.occ.serializers import serialize_shape


def default_callable(i, n):
    pass
    # logger.info(f"[{i}/{n}] ")


def step_file_to_ifc_file(
    step_file: pathlib.Path,
    ifc_file_path: str | pathlib.Path | None = None,
    progress_callback: Callable[[int, int], None] = default_callable,
    include_colors=False,
) -> None:
    if ifc_file_path is None:
        ifc_file_path = step_file.with_suffix(".ifc")
    else:
        ifc_file_path = pathlib.Path(ifc_file_path)

    a = ada.Assembly("AdaStep")
    f = a.ifc_store.f
    a.ifc_store.writer = IfcWriter(a.ifc_store)
    a.ifc_store.writer.sync_spatial_hierarchy()
    body_context = a.ifc_store.get_context("Body")

    shape_placement = create_local_placement(f)
    relating_elements = []

    step = StepStore(step_file)
    num_serialized = 0
    num_tesselated = 0

    for i, step_shape in enumerate(step.iter_all_shapes(include_colors=include_colors), start=1):
        shape = step_shape.shape
        tot_num = step_shape.num_tot_entities

        shape_str = serialize_shape(shape)
        res = ifcopenshell_wrapper.serialise(f.schema, shape_str, True)
        name = f"Shape{i:04d}"

        progress_callback(i, tot_num)

        if res is None:
            logger.info(f"Tesselated {name} [{i}/{tot_num}] to IFC")
            res = ifcopenshell_wrapper.tesselate(f.schema, shape_str, Units.get_general_point_tol(Units.M))
            num_tesselated += 1
        else:
            num_serialized += 1
            logger.info(f"Serialized {name} [{i}/{tot_num}] to IFC")

        if res is None:
            logger.info(f"Could not serialize OR tesselate shape {shape} [{i}/{tot_num}]")
            continue

        entity_instance = ifcopenshell.geom.entity_instance(res)
        entity_instance.Representations[0].ContextOfItems = body_context

        prod_def_shp = f.add(entity_instance)
        proxy = f.create_entity(
            "IfcBuildingElementProxy",
            GlobalId=create_guid(),
            OwnerHistory=a.ifc_store.owner_history,
            Name=name,
            Description=name,
            ObjectType=None,
            ObjectPlacement=shape_placement,
            Representation=prod_def_shp,
        )
        if step_shape.color is not None:
            add_colour(f, prod_def_shp, str(step_shape.color), step_shape.color)

        relating_elements.append(proxy)

    logger.info(f"Serialized {num_serialized} shapes to IFC")
    logger.info(f"Tesselated {num_tesselated} shapes to IFC")

    logger.info(f"Adding {len(relating_elements)} elements to spatial container")
    a.ifc_store.writer.add_related_elements_to_spatial_container(relating_elements, a.guid)

    logger.info("Validating IFC file")
    ifcopenshell.validate.validate(f, logger)

    logger.info(f"Writing to file {ifc_file_path}")
    a.ifc_store.f.write(str(ifc_file_path))
