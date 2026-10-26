"""A comparison's difference as something to show: one field per compared field and step,
centred on zero, with the model's own properties and sets kept."""

import numpy as np

from ada.fem.results.artefacts.manifest import _centred_on_zero
from ada.fem.results.common import (
    ElementBlock,
    ElementInfo,
    FEAResult,
    FEATypes,
    FemNodes,
    Mesh,
)
from ada.fem.results.field_data import ElementFieldData, NodalFieldData
from ada.fem.shapes.definitions import LineShapes


def _result(scale: float, name: str) -> FEAResult:
    mesh = Mesh(
        elements=[
            ElementBlock(
                elem_info=ElementInfo(type=LineShapes.LINE, source_software=FEATypes.SESAM, source_type=15),
                node_refs=np.array([[1, 2]]),
                identifiers=np.array([7]),
            )
        ],
        nodes=FemNodes(coords=np.array([[0.0, 0, 0], [1.0, 0, 0]]), identifiers=np.array([1, 2])),
    )
    results = []
    for step in (1, 2):
        results.append(
            NodalFieldData("U", step, ["X", "Y"], np.array([[1, scale * step, -scale], [2, 0.0, 2 * scale * step]]))
        )
    # A model property: the element's material id, the same in both results.
    results.append(ElementFieldData("props.material", 1, ["MATERIAL"], np.array([[7, 0, 3.0]])))
    res = FEAResult(name, FEATypes.SESAM, results, mesh, step_name_map={1: "LC1", 2: "COMB1"})
    res.sesam_case_names = {1: "LC1", 2: "COMB1"}
    return res


def test_difference_fields_cover_every_step_and_say_what_they_are():
    diff = _result(1.0, "a").compare(_result(1.5, "b")).to_fea_result()
    u = sorted((f for f in diff.results if f.name == "U"), key=lambda f: f.step)
    assert [f.step for f in u] == [1, 2]
    assert all(f.difference for f in u)
    np.testing.assert_allclose(u[1].values[:, 1:], [[1.0, -0.5], [0.0, 2.0]])


def test_property_fields_and_deck_metadata_are_the_bases():
    base = _result(1.0, "a")
    diff = base.compare(_result(1.5, "b")).to_fea_result()
    (mat,) = [f for f in diff.results if f.name == "props.material"]
    assert not mat.difference
    assert mat.values[0, 2] == 3.0  # the id, not 0 (3 - 3)
    assert diff.sesam_case_names == base.sesam_case_names


def test_a_model_compared_with_itself_is_zero_everywhere():
    a = _result(1.0, "a")
    diff = a.compare(_result(1.0, "a")).to_fea_result()
    for f in diff.results:
        if f.difference:
            assert not np.any(f.values[:, 1:])


def test_difference_ranges_are_centred_on_zero():
    assert _centred_on_zero({"X": [-1.0, 3.0], "magnitude": [0.5, 3.0]}) == {
        "X": [-3.0, 3.0],
        "magnitude": [0.5, 3.0],
    }
    assert _centred_on_zero({"X": [0.0, 0.0]}) == {"X": [-0.0, 0.0]}
