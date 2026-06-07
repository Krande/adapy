"""Sesam BNMASS writer: MASS / ROTARYI / NONSTRUCTURAL mass types.

Per the Sesam Input Interface File spec, BNMASS gives mass per DOF — 1-3 translational,
4-6 rotational (rotary inertia). This pins how each adapy Mass type maps onto those six slots.
"""

import ada
from ada.fem import FemSet, Mass
from ada.fem.formats.sesam.write.write_masses import _bnmass_components
from ada.fem.shapes.definitions import MassTypes


def _mass(mass_value, mass_type, n_nodes=1, ptype=None):
    a = ada.Assembly() / (ada.Part("p"),)
    p = a.get_by_name("p")
    nodes = [p.fem.nodes.add(ada.Node((float(i), 0, 0), i + 1)) for i in range(n_nodes)]
    fs = p.fem.add_set(FemSet("ms", nodes, FemSet.TYPES.NSET))
    return Mass("m", fs, mass_value, mass_type, ptype, parent=p.fem)


def test_bnmass_translational():
    comps = _bnmass_components(_mass(12.0, MassTypes.MASS), 1)
    assert comps == [12.0, 12.0, 12.0, 0.0, 0.0, 0.0]


def test_bnmass_translational_anisotropic():
    m = _mass([1.0, 2.0, 3.0], MassTypes.MASS, ptype=Mass.PTYPES.ANISOTROPIC)
    assert _bnmass_components(m, 1) == [1.0, 2.0, 3.0, 0.0, 0.0, 0.0]


def test_bnmass_rotary_inertia_in_rotational_dofs():
    # ROTARYI populates DOF 4-6, leaving translational DOFs zero.
    comps = _bnmass_components(_mass([4.0, 5.0, 6.0], MassTypes.ROTARYI), 1)
    assert comps == [0.0, 0.0, 0.0, 4.0, 5.0, 6.0]


def test_bnmass_nonstructural_lumped_per_node():
    # A region (3 nodes) total mass of 30 lumps to 10 per node, translational.
    comps = _bnmass_components(_mass(30.0, MassTypes.NONSTRUCTURAL, n_nodes=3), 3)
    assert comps == [10.0, 10.0, 10.0, 0.0, 0.0, 0.0]
