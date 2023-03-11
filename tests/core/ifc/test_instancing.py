import numpy as np
import pytest

import ada


@pytest.fixture
def test_instancing_dir(ifc_test_dir):
    return ifc_test_dir / "instancing"


def test_ifc_instancing(test_instancing_dir):
    a = ada.Assembly("my_test_assembly")
    p = ada.Part("MyBoxes")
    box = p.add_shape(ada.PrimBox("Cube_original", (0, 0, 0), (1, 1, 1)))
    for x in range(1, 10):
        for y in range(1, 10):
            for z in range(1, 10):
                origin = np.array([x, y, z]) * 1.1 + box.placement.origin
                p.add_instance(box, ada.Placement(origin))

    _ = (a / p).to_ifc(test_instancing_dir / "my_test.ifc", file_obj_only=True)
