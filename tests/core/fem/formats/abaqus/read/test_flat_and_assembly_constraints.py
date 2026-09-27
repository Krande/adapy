"""One model written flat and written as an ``*Assembly`` reads the same constraints.

A deck with no ``*Assembly`` block goes through ``add_fem_without_assembly`` ->
``get_fem_from_bulk_str``, which reads its constraints into the *part's* FEM; a deck with one
reads them from the assembly bulk into the *assembly's* FEM. Two code paths, two places to put
the result, and nothing held them to the same answer -- the assembly path used to lose the whole
deck to an ``*MPC`` naming ``P-1.4`` (see ``test_read_mpc_instance_nodes``) while the flat deck
read all five of its constraints.

Written as a parity test rather than two separate ones because what matters is not how many
constraints either path finds on its own but that the choice of deck layout -- which is a
CAE export option, not a modelling decision -- does not change the model that comes out.
"""

from __future__ import annotations

import ada

# 1 x 4 grid of shells, its ends tied to each other, its middle rigid, plus a coupling, an
# equation and an MPC: one constraint of every type the reader builds from a keyword that can
# appear in either deck layout. (``*Shell to Solid Coupling`` is left out -- it needs a solid.)
_MESH = """\
*Node
      1,          0.,          0.,          0.
      2,          1.,          0.,          0.
      3,          1.,          1.,          0.
      4,          0.,          1.,          0.
      5,          2.,          0.,          0.
      6,          2.,          1.,          0.
      7,          3.,          0.,          0.
      8,          3.,          1.,          0.
      9,          4.,          0.,          0.
     10,          4.,          1.,          0.
*Element, type=S4R
1, 1, 2, 3, 4
2, 2, 5, 6, 3
3, 5, 7, 8, 6
4, 7, 9, 10, 8
*Elset, elset=plates, generate
 1, 4, 1
*Elset, elset=left
 1,
*Elset, elset=mid
 2, 3
*Elset, elset=right
 4,
** Section: Plate
*Shell Section, elset=plates, material=Steel
0.01, 5
"""

_REF_NODES = """\
*Node
    100,          5.,         0.5,          0.
    101,          6.,         0.5,          0.
*Nset, nset=rp
 100,
*Nset, nset=rp2
 101,
"""

_CONSTRAINTS = """\
** Constraint: Tie-1
*Tie, name=Tie-1, adjust=yes
leftsurf, rightsurf
** Constraint: Rigid-1
*Rigid Body, ref node=rp, elset={mid}
** Constraint: Coup-1
*Coupling, constraint name=Coup-1, ref node=rp2, surface=rightsurf
*Kinematic
1, 3
** Constraint: Eq-1
*Equation
2
{p}2, 1, 1.0, {p}3, 1, -1.0
** Constraint: MPC-1
*MPC
BEAM, {p}4, {p}6
"""

_MATERIAL = """\
*Material, name=Steel
*Elastic
 2.1e+11, 0.3
"""

FLAT_DECK = (
    "*Heading\n"
    + _MESH
    + _REF_NODES
    + """\
*Surface, type=ELEMENT, name=leftsurf
left, S1
*Surface, type=ELEMENT, name=rightsurf
right, S1
"""
    + _CONSTRAINTS.format(p="", mid="mid")
    + _MATERIAL
)

ASSEMBLY_DECK = (
    "*Heading\n*Part, name=P\n"
    + _MESH
    + "*End Part\n*Assembly, name=Assembly\n*Instance, name=P-1, part=P\n*End Instance\n"
    + _REF_NODES
    + """\
*Elset, elset=leftE, instance=P-1
 1,
*Elset, elset=midE, instance=P-1
 2, 3
*Elset, elset=rightE, instance=P-1
 4,
*Surface, type=ELEMENT, name=leftsurf
leftE, S1
*Surface, type=ELEMENT, name=rightsurf
rightE, S1
"""
    + _CONSTRAINTS.format(p="P-1.", mid="midE")
    + "*End Assembly\n"
    + _MATERIAL
)


def _constraints(assembly) -> dict[str, str]:
    """Every constraint the deck produced, wherever the reader chose to keep it: the flat path
    puts them on the part FEM, the assembly path on the assembly FEM."""
    found = {c.name: c.type for p in assembly.get_all_parts_in_assembly() for c in p.fem.constraints.values()}
    found.update({c.name: c.type for c in assembly.fem.constraints.values()})
    return found


def test_flat_and_assembly_decks_read_the_same_constraints(tmp_path):
    flat = tmp_path / "flat.inp"
    flat.write_text(FLAT_DECK)
    assem = tmp_path / "assembly.inp"
    assem.write_text(ASSEMBLY_DECK)

    flat_cons = _constraints(ada.from_fem(flat, "abaqus"))
    assem_cons = _constraints(ada.from_fem(assem, "abaqus"))

    assert flat_cons == {
        "Tie-1": "tie",
        "Rigid-1": "rigid body",
        "Coup-1": "coupling",
        "Eq-1": "equation",
        "MPC-1": "mpc",
    }
    assert assem_cons == flat_cons
