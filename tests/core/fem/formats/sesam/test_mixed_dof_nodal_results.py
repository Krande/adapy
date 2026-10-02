"""Nodal displacements of a model mixing 3-dof and 6-dof nodes.

Sestra writes an RVNODDIS record with as many components as the node has dofs: three for a node only solids
touch, six for a shell/beam node or a coupling's reference node. Such a model -- a solid beam end supported
through a coupled reference node, say -- used to fail to read ("setting an array element with a sequence ...
inhomogeneous shape").
"""

import numpy as np

from ada.fem.formats.sesam.results.read_sif import get_nodal_results


def test_three_and_six_component_records_read_together():
    # nfield, ires, inod, irdva, itrans, U1..U3[, U4..U6]
    solid_node = [8.0, 1.0, 1.0, 1.0, 0.0, 0.1, 0.2, 0.3]
    ref_node = [11.0, 1.0, 2.0, 1.0, 0.0, 0.4, 0.5, 0.6, 0.01, 0.02, 0.03]
    (field,) = get_nodal_results(["RVNODDIS", solid_node, ref_node])

    assert field.values.shape == (2, 7)
    np.testing.assert_allclose(field.values[0], [1, 0.1, 0.2, 0.3, 0.0, 0.0, 0.0])
    np.testing.assert_allclose(field.values[1], [2, 0.4, 0.5, 0.6, 0.01, 0.02, 0.03])
