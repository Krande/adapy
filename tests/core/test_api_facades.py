import pytest


@pytest.mark.parametrize(
    "facade, deep, name",
    [
        ("ada.api.beams", "ada.api.beams.beam_tapered", "TaperTypes"),
        ("ada.api.primitives", "ada.api.primitives.bool_half_space", "BoolHalfSpace"),
        ("ada.api.spatial", "ada.api.spatial.equipment", "Equipment"),
        ("ada.api.systems", "ada.api.systems.model", "SystemModel"),
    ],
)
def test_the_package_exports_what_its_modules_define(facade, deep, name):
    package = __import__(facade, fromlist=[name])
    module = __import__(deep, fromlist=[name])  # the deep path keeps working
    assert getattr(package, name) is getattr(module, name)
    assert name in package.__all__
