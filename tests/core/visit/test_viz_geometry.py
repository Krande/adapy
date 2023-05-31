import pytest

from ada import Assembly, Beam, Plate


@pytest.fixture
def model_with_components() -> Assembly:
    components = [
        Beam("bm1", n1=[0, 0, 0], n2=[2, 0, 0], sec="IPE220", color="red"),
        Beam("bm2", n1=[0, 0, 1], n2=[2, 0, 1], sec="HP220x10", color="blue"),
        Beam("bm3", n1=[0, 0, 2], n2=[2, 0, 2], sec="BG800x400x20x40", color="green"),
        Beam("bm4", n1=[0, 0, 3], n2=[2, 0, 3], sec="CIRC200", color="green"),
        Beam("bm5", n1=[0, 0, 4], n2=[2, 0, 4], sec="TUB200x10", color="green"),
        Plate(
            "pl1",
            [(0, 0, 0), (0, 0, 1), (0, 1, 1), (0, 1, 0)],
            0.01,
            use3dnodes=True,
        ),
    ]
    return Assembly("my_test_assembly") / components


def test_viz_structural_glb(model_with_components):
    model_with_components.to_gltf("temp/model_merged.glb", embed_meta=True, merge_by_color=True)
    model_with_components.to_gltf("temp/model.glb", embed_meta=True)
