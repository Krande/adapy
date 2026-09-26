"""An ``*MPC`` inside an ``*Assembly`` names its nodes instance-qualified: ``BEAM, P-1.4, P-1.6``.

``get_set_from_assembly`` resolves ``P-1.4`` to the *node* -- the numeric branch of it hands back
``part.fem.nodes.from_id(4)``, not a set -- and ``mpc_nodes`` fed every non-integer reference to
``ref.members``. So the form Abaqus/CAE writes for every assembly-level MPC raised
``AttributeError: 'NodeProxy' object has no attribute 'members'`` from inside
``get_constraints_from_inp``, which the caller only guards against ``KeyError``: the whole import
died, not just the constraint. The same model written flat, with bare node ids, read fine.
"""

from __future__ import annotations

import ada

_DECK = """\
*Heading
*Part, name=P
*Node
      1,          0.,          0.,          0.
      2,          1.,          0.,          0.
      3,          2.,          0.,          0.
      4,          3.,          0.,          0.
*Element, type=B31
1, 1, 2
2, 2, 3
3, 3, 4
*Elset, elset=beams, generate
 1, 3, 1
** Section: Beam
*Beam Section, elset=beams, material=Steel, section=BOX
 0.1, 0.1, 0.01, 0.01, 0.01, 0.01
 0.,0.,1.
*Nset, nset=tip
 4,
*End Part
*Assembly, name=Assembly
*Instance, name=P-1, part=P
*End Instance
** Constraint: MPC-1
*MPC
 BEAM, P-1.1, P-1.3
** Constraint: MPC-2
*MPC
 TIE, P-1.2, P-1.tip
*End Assembly
*Material, name=Steel
*Elastic
 2.1e+11, 0.3
"""


def test_mpc_reads_instance_qualified_node_ids(tmp_path):
    inp = tmp_path / "mpc_instance.inp"
    inp.write_text(_DECK)

    a = ada.from_fem(inp, "abaqus")

    beam = a.fem.constraints["MPC-1"]
    assert beam.type == beam.TYPES.MPC
    assert beam.mpc_type == "BEAM"
    assert [n.id for n in beam.m_set.members] == [1]
    assert [n.id for n in beam.s_set.members] == [3]

    # A set name on the same data line still expands to the set's members: the two forms live
    # side by side in one deck and neither may shadow the other.
    tie = a.fem.constraints["MPC-2"]
    assert [n.id for n in tie.m_set.members] == [2]
    assert [n.id for n in tie.s_set.members] == [4]
