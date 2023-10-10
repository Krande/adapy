import numpy as np

from ada.fem.formats.general import FEATypes
from ada.fem.formats.vtu.write import write_to_vtu_file
from ada.fem.results.common import ElementInfo, ElementBlock, FemNodes
from ada.fem.shapes.definitions import LineShapes, SolidShapes, ShellShapes


def test_basic_vtu_write():
    # Sample usage
    elem_info = ElementInfo(type=ShellShapes.TRI, source_software=FEATypes.CODE_ASTER, source_type="ELGA3")
    element_block = ElementBlock(
        elem_info=elem_info,
        node_refs=np.array([[0, 1, 3], [1, 2, 3]], dtype=np.int32),
        identifiers=np.array([1, 2], dtype=np.int32),
    )
    fem_nodes = FemNodes(
        coords=np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]], dtype=np.float32),
        identifiers=np.array([1, 2, 3, 4], dtype=np.int32),
    )

    point_data = {"Temperature": np.array([30.5, 32.5, 34.0, 36.0], dtype=np.float32)}
    cell_data = {"Stress": np.array([1.0, 2.0], dtype=np.float32)}

    write_to_vtu_file(fem_nodes, [element_block], point_data, cell_data, "temp/basic_mesh.vtu")


def test_mixed_mesh():
    # Sample usage
    element_blocks = [
        ElementBlock(
            elem_info=ElementInfo(type=ShellShapes.TRI, source_software=FEATypes.GMSH, source_type="your_source_type"),
            node_refs=np.array([[0, 1, 2], [2, 3, 0]]),
            identifiers=np.array([1, 2]),
        ),
        ElementBlock(
            elem_info=ElementInfo(type=LineShapes.LINE, source_software=FEATypes.GMSH, source_type="your_source_type"),
            node_refs=np.array([[0, 1], [1, 2]]),
            identifiers=np.array([3, 4]),
        ),
    ]
    fem_nodes = FemNodes(
        coords=np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]], dtype=np.float32),
        identifiers=np.array([1, 2, 3, 4]),
    )

    point_data = {"Temperature": np.array([30.5, 32.5, 34.0, 36.0], dtype=np.float32)}
    cell_data = {"Stress": np.array([1.0, 2.0, 0.5, 0.8], dtype=np.float32)}

    write_to_vtu_file(fem_nodes, element_blocks, point_data, cell_data, "mixed_mesh.vtu")
