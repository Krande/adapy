from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Iterable

from OCC.Core.IFSelect import IFSelect_ItemsByEntity, IFSelect_RetDone
from OCC.Core.Interface import Interface_Static
from OCC.Core.STEPCAFControl import STEPCAFControl_Reader
from OCC.Core.STEPControl import STEPControl_Reader
from OCC.Core.TDocStd import TDocStd_Document
from OCC.Core.TopoDS import TopoDS_Compound, TopoDS_Shape
from OCC.Core.XCAFDoc import XCAFDoc_DocumentTool
from OCC.Extend.TopologyUtils import TopologyExplorer, list_of_shapes_to_compound

from ada.base.units import Units
from ada.cadit.step.read.reader_utils import read_step_file_with_names_colors
from ada.config import logger
from ada.occ.store import OccShape
from ada.occ.utils import get_boundingbox
from ada.visit.colors import Color


class StepStore:
    def __init__(self, filepath, verbosity=True, store_units: Units | str = Units.M, include_wires=False):
        self.filepath = filepath
        if isinstance(store_units, str):
            store_units = Units.from_str(store_units)

        self.store_units = store_units
        self.verbosity = verbosity
        self.include_wires = include_wires
        self.step_reader: STEPControl_Reader | STEPCAFControl_Reader | None = None

        # For OCAF
        self.shape_tool: XCAFDoc_DocumentTool.ShapeTool = None
        self.color_tool: XCAFDoc_DocumentTool.ColorTool = None
        self.doc: TDocStd_Document | None = None

    def create_step_reader(self, use_ocaf=False) -> STEPControl_Reader | STEPCAFControl_Reader:
        filename = str(self.filepath)
        if not os.path.isfile(filename):
            raise FileNotFoundError(f"{filename} not found.")

        if not use_ocaf:
            step_reader = STEPControl_Reader()
        else:
            self.doc = TDocStd_Document("XmlOcaf")
            self.shape_tool = XCAFDoc_DocumentTool.ShapeTool(self.doc.Main())
            self.color_tool = XCAFDoc_DocumentTool.ColorTool(self.doc.Main())
            step_reader = STEPCAFControl_Reader()
            step_reader.SetColorMode(True)
            step_reader.SetLayerMode(True)
            step_reader.SetNameMode(True)
            step_reader.SetMatMode(True)
            step_reader.SetGDTMode(True)

        Interface_Static.SetCVal("xstep.cascade.unit", self.store_units.value.upper())

        logger.info(f"Reading STEP file: '{filename}' [{self.store_units.value=}] [{use_ocaf=}]")
        status = step_reader.ReadFile(filename)

        if status != IFSelect_RetDone:  # check status
            raise AssertionError("Error: can't read file.")

        if self.verbosity and not use_ocaf:
            failsonly = False
            step_reader.PrintCheckLoad(failsonly, IFSelect_ItemsByEntity)
            step_reader.PrintCheckTransfer(failsonly, IFSelect_ItemsByEntity)

        if not use_ocaf:
            transfer_result = step_reader.TransferRoots()
        else:
            transfer_result = step_reader.Transfer(self.doc)

        if not transfer_result:
            raise AssertionError("Transfer failed.")

        self.step_reader = step_reader
        self.check_units()
        return step_reader

    def check_units(self):
        step_reader = self.step_reader
        if step_reader is None:
            raise AssertionError("step_reader is None")
        if isinstance(step_reader, STEPCAFControl_Reader):
            step_reader = step_reader.Reader()

        if self.store_units == Units.M:
            if step_reader.SystemLengthUnit() != 1000.0:
                raise AssertionError("System unit is not M.")
        elif self.store_units == Units.MM:
            if step_reader.SystemLengthUnit() != 1.0:
                raise AssertionError("System unit is not MM.")

    def get_root_shape(self, use_ocaf=False) -> TopoDS_Shape | TopoDS_Compound | None:
        """Get root shape in STEP file."""
        step_reader = self.step_reader
        if step_reader is None:
            step_reader = self.create_step_reader(use_ocaf)
            if isinstance(step_reader, STEPCAFControl_Reader):
                step_reader = step_reader.ChangeReader()
            _nbs = step_reader.NbShapes()
        else:
            if isinstance(step_reader, STEPCAFControl_Reader):
                step_reader = step_reader.ChangeReader()
                _nbs = step_reader.NbRootsForTransfer()
            else:
                _nbs = step_reader.NbShapes()

        if _nbs == 0:
            raise AssertionError("No shape to transfer.")
        if _nbs == 1:  # most cases
            return step_reader.Shape(1)
        if _nbs > 1:
            logger.info("Number of shapes:", _nbs)
            shapes = []
            # loop over root shapes
            for k in range(1, _nbs + 1):
                new_shp = step_reader.Shape(k)
                if not new_shp.IsNull():
                    shapes.append(new_shp)
            compound, result = list_of_shapes_to_compound(shapes)
            if not result:
                logger.warning("all shapes were not added to the compound")
            return compound

        return None

    def get_num_shapes(self, root_shape=None, use_ocaf=False) -> int:
        if root_shape is None:
            root_shape = self.get_root_shape(use_ocaf)

        t = TopologyExplorer(root_shape)

        # Find the total number of shapes to be yielded
        num_solids = t.number_of_solids()
        num_shells = t.number_of_shells()

        num_shapes = num_solids + num_shells
        if self.include_wires:
            num_wires = t.number_of_wires()
            num_shapes += num_wires

        return num_shapes

    def _iter_subshapes(self, root_shape: TopoDS_Shape) -> Iterable[TopoDS_Shape]:
        t = TopologyExplorer(root_shape)
        # Yield the shapes
        for solid in t.solids():
            yield solid
        for shell in t.shells():
            yield shell

        if self.include_wires:
            for wire in t.wires():
                yield wire

    def iter_all_shapes(self, include_colors=False) -> Iterable[OccShape]:
        props_map = {}
        if include_colors:
            if not isinstance(self.step_reader, STEPCAFControl_Reader):
                self.create_step_reader(True)
            num_shapes = self.get_num_shapes()
            for topods_shape, (label, c_quant) in read_step_file_with_names_colors(self).items():
                color = Color(c_quant.Red(), c_quant.Green(), c_quant.Blue())
                yield OccShape(topods_shape, color, num_shapes, label)
                del topods_shape
        else:
            num_shapes = self.get_num_shapes()
            for i, shape in enumerate(self._iter_subshapes(self.get_root_shape())):
                props = props_map.get(i, None)
                if props is None:
                    yield OccShape(shape, None, num_shapes)
                else:
                    yield OccShape(shape, props.color, num_shapes, props.name)

    def get_bbox(self):
        shape = self.get_root_shape()
        return get_boundingbox(shape)


@dataclass
class EntityProps:
    hash: str
    name: str
    color: tuple[float, float, float]
