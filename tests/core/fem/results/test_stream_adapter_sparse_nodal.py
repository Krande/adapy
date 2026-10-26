"""A nodal step listing fewer nodes than the mesh (Sestra writes no displacement record for a node
without degrees of freedom) is baked as one row per mesh point, placed by node id."""

import numpy as np

from ada.fem.formats.general import FEATypes
from ada.fem.results.artefacts import FEAResultStreamAdapter
from ada.fem.results.common import ElementBlock, ElementInfo, FEAResult, FemNodes, Mesh
from ada.fem.results.field_data import NodalFieldData, NodalFieldType
from ada.fem.shapes.definitions import LineShapes


def _result(fields):
    nodes = FemNodes(coords=np.array([[0.0, 0, 0], [1.0, 0, 0], [2.0, 0, 0]]), identifiers=np.array([10, 20, 30]))
    block = ElementBlock(
        elem_info=ElementInfo(type=LineShapes.LINE, source_software=FEATypes.SESAM, source_type=15),
        node_refs=np.array([[10, 20], [20, 30]]),
        identifiers=np.array([1, 2]),
    )
    return FEAResult("sparse", FEATypes.SESAM, fields, Mesh(elements=[block], nodes=nodes))


def _steps(result, name):
    adapter = FEAResultStreamAdapter(result)
    return [sv.values for sv in adapter.iter_field_steps(name)]


def test_a_full_step_passes_through_and_a_sparse_one_is_placed_by_node_id():
    full = NodalFieldData("U", 1, ["X"], np.array([[10, 1.0], [20, 2.0], [30, 3.0]]), field_type=NodalFieldType.DISP)
    # Step 2 lists nodes 30 and 10 only, out of mesh order.
    sparse = NodalFieldData("U", 2, ["X"], np.array([[30, 7.0], [10, 5.0]]), field_type=NodalFieldType.DISP)

    first, second = _steps(_result([full, sparse]), "U")

    np.testing.assert_array_equal(first.ravel(), [1.0, 2.0, 3.0])
    # Node 20 has no record: no degree of freedom, so no displacement.
    np.testing.assert_array_equal(second.ravel(), [5.0, 0.0, 7.0])


def test_an_element_step_listing_fewer_elements_is_placed_by_label():
    from ada.fem.results.field_data import ElementFieldData, FieldPosition

    def forces(step, rows):
        return ElementFieldData(
            "N", step, ["NXX"], np.array(rows, dtype=float), field_pos=FieldPosition.INT, elem_type=LineShapes.LINE
        )

    full = forces(1, [[1, 1, 10.0], [1, 2, 11.0], [2, 1, 20.0], [2, 2, 21.0]])
    sparse = forces(2, [[2, 1, 30.0], [2, 2, 31.0]])  # step 2 loads element 2 only
    adapter = FEAResultStreamAdapter(_result([full, sparse]))
    (spec,) = adapter.element_field_specs()

    first, second = (sv.values for sv in adapter.iter_element_field_steps(spec))

    assert spec.n_elements == 2
    np.testing.assert_array_equal(first[:, :, 0], [[10.0, 11.0], [20.0, 21.0]])
    assert np.isnan(second[0]).all(), "element 1 has no value in step 2"
    np.testing.assert_array_equal(second[1, :, 0], [30.0, 31.0])


def test_element_ids_beyond_float32_precision_survive_the_bake():
    """float32 holds integers exactly only up to 2**24: element 21000001 must not come back as
    21000000 (a superelement assembly's ids are prefixed into that range)."""
    from ada.fem.results.field_data import ElementFieldData, FieldPosition

    big = 21_000_001
    result = _result([])
    result.mesh.elements[0].identifiers = np.array([big, big + 2])
    field = ElementFieldData(
        "N",
        1,
        ["NXX"],
        np.array([[big + 2, 1, 1.0], [big + 2, 2, 2.0], [big, 1, 3.0], [big, 2, 4.0]], dtype=float),
        field_pos=FieldPosition.INT,
        elem_type=LineShapes.LINE,
    )
    later = ElementFieldData(
        "N",
        2,
        ["NXX"],
        np.array([[big, 1, 5.0], [big, 2, 6.0]]),
        field_pos=FieldPosition.INT,
        elem_type=LineShapes.LINE,
    )
    result.results = [field, later]
    adapter = FEAResultStreamAdapter(result)
    (spec,) = adapter.element_field_specs()

    first, second = (sv.values for sv in adapter.iter_element_field_steps(spec))

    np.testing.assert_array_equal(first[:, :, 0], [[1.0, 2.0], [3.0, 4.0]])
    assert np.isnan(second[0]).all()
    np.testing.assert_array_equal(second[1, :, 0], [5.0, 6.0])


def test_a_missing_node_of_any_other_field_has_no_value():
    full = NodalFieldData("S", 1, ["VM"], np.array([[10, 1.0], [20, 2.0], [30, 3.0]]))
    sparse = NodalFieldData("S", 2, ["VM"], np.array([[10, 4.0], [30, 6.0]]))

    _, second = _steps(_result([full, sparse]), "S")

    assert second[0, 0] == 4.0 and second[2, 0] == 6.0
    assert np.isnan(second[1, 0])
