import base64
import pathlib
import struct
import xml.etree.ElementTree as ET

import numpy as np

from ada.fem.results.common import ElementBlock, FemNodes
from ada.fem.shapes.definitions import LineShapes, ShellShapes, SolidShapes


def array_to_binary(array, dtype):
    binary_data = struct.pack(f"{len(array)}{dtype}", *array)
    header = struct.pack("I", len(binary_data))
    return base64.b64encode(header + binary_data).decode()


# Mapping between custom shape enums and VTK types
# https://vtk.org/doc/nightly/html/vtkCellType_8h_source.html
VTK_TYPE_MAP = {
    LineShapes.LINE: 3,
    LineShapes.LINE3: 21,
    ShellShapes.TRI: 5,
    ShellShapes.TRI6: 22,
    ShellShapes.QUAD: 9,
    ShellShapes.QUAD8: 23,
    SolidShapes.TETRA: 10,
    SolidShapes.HEX8: 12,
    SolidShapes.TETRA10: 24,
    SolidShapes.HEX20: 25,
    SolidShapes.WEDGE: 13,
    SolidShapes.WEDGE15: 26,
}

# New mapping dictionary
numpy_to_vtu_type = {
    np.dtype(np.float32): "Float32",
    np.dtype(np.float64): "Float64",
    np.dtype(np.int8): "Int8",
    np.dtype(np.int16): "Int16",
    np.dtype(np.int32): "Int32",
    np.dtype(np.int64): "Int64",
    np.dtype(np.uint8): "UInt8",
    np.dtype(np.uint16): "UInt16",
    np.dtype(np.uint32): "UInt32",
    np.dtype(np.uint64): "UInt64",
}

numpy_to_struct_type = {
    np.dtype(np.float32): "f",
    np.dtype(np.float64): "d",
    np.dtype(np.int8): "b",
    np.dtype(np.int16): "h",
    np.dtype(np.int32): "i",
    np.dtype(np.int64): "q",
    np.dtype(np.uint8): "B",
    np.dtype(np.uint16): "H",
    np.dtype(np.uint32): "I",
    np.dtype(np.uint64): "Q",
}


def write_to_vtu_object(nodes: FemNodes, element_blocks: list[ElementBlock], point_data: dict, cell_data: dict):
    all_node_refs = []
    all_types = []
    offsets = []
    offset = 0

    for block in element_blocks:
        vtk_type = VTK_TYPE_MAP.get(block.elem_info.type)
        if vtk_type is None:
            raise ValueError(f"Element type {block.elem_info.type} not supported by VTK")
        # Block node_refs starts at 1, but VTK starts at 0
        refs = block.node_refs - 1
        for refs in refs:
            all_node_refs.extend(refs)
            all_types.append(vtk_type)
            offset += len(refs)
            offsets.append(offset)

    root = ET.Element("VTKFile", type="UnstructuredGrid", version="1.0", byte_order="LittleEndian")
    unstructured_grid = ET.SubElement(root, "UnstructuredGrid")
    piece = ET.SubElement(
        unstructured_grid, "Piece", NumberOfPoints=str(nodes.coords.shape[0]), NumberOfCells=str(len(all_types))
    )

    # Points
    points_element = ET.SubElement(piece, "Points")
    data_array = ET.SubElement(points_element, "DataArray", type="Float32", NumberOfComponents="3", format="binary")
    data_array.text = array_to_binary(nodes.coords.flatten(), "f")

    # Cells
    cells_element = ET.SubElement(piece, "Cells")

    # Connectivity
    data_array = ET.SubElement(cells_element, "DataArray", type="Int32", Name="connectivity", format="binary")
    data_array.text = array_to_binary(all_node_refs, "i")

    # Offsets
    data_array = ET.SubElement(cells_element, "DataArray", type="Int32", Name="offsets", format="binary")
    data_array.text = array_to_binary(offsets, "i")

    # Types
    data_array = ET.SubElement(cells_element, "DataArray", type="UInt8", Name="types", format="binary")
    data_array.text = array_to_binary(all_types, "B")

    # Point Data
    point_data_element = ET.SubElement(piece, "PointData")
    for key, value in point_data.items():
        data_type = numpy_to_vtu_type[np.dtype(value.dtype)]
        num_components = str(value.shape[1] if len(value.shape) > 1 else 1)
        if num_components == "6":
            num_components = "3"
            value = value[:, :3]
        struct_type = numpy_to_struct_type[np.dtype(value.dtype)]
        data_array = ET.SubElement(
            point_data_element,
            "DataArray",
            type=data_type,
            NumberOfComponents=num_components,
            Name=key,
            format="binary",
        )
        data_array.text = array_to_binary(value.flatten(), struct_type)

    # Cell Data
    cell_data = {}
    cell_data_element = ET.SubElement(piece, "CellData")
    for key, value_ in cell_data.items():
        if len(value_) != 1:
            raise ValueError("Cell data must be a single value per cell")
        value = value_[0]
        data_type = numpy_to_vtu_type[np.dtype(value.dtype)]
        struct_type = numpy_to_struct_type[np.dtype(value.dtype)]
        num_components = str(value.shape[1] if len(value.shape) > 1 else 1)
        data_array = ET.SubElement(
            cell_data_element, "DataArray", type=data_type, NumberOfComponents=num_components, Name=key, format="binary"
        )
        data_array.text = array_to_binary(value.flatten(), struct_type)

    return ET.ElementTree(root)


def write_to_vtu_file(
    nodes: FemNodes, element_blocks: list[ElementBlock], point_data: dict, cell_data: dict, filename: str | pathlib.Path
):
    tree = write_to_vtu_object(nodes, element_blocks, point_data, cell_data)

    if isinstance(filename, str):
        filename = pathlib.Path(filename).resolve().absolute()

    filename.parent.mkdir(parents=True, exist_ok=True)
    with open(filename, "wb") as f:
        f.write(b'<?xml version="1.0"?>\n')
        tree.write(f)
