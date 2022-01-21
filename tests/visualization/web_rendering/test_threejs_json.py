import pytest

from ada import Assembly, Beam, Part
from ada.visualize.write.write_threejs_json import to_three_json


@pytest.fixture
def model():
    bm1 = Beam("Bm1", (0, 0, 0), (1, 0, 0), "IPE300", colour="red")
    bm2 = Beam("Bm2", (1, 0, 0), (1, 1, 0), "IPE300", colour="blue")
    return Assembly("MyAssembly", project="007600") / (Part("MyPart") / [bm1, bm2])


def test_basic_model(model, visualization_test_dir):
    to_three_json(model, visualization_test_dir / "my_threejs.json")
    # model.to_ifc("temp/my_ifc.ifc")
