"""An assembly-level ``*Elset, instance=...`` resolves against the instance's elements.

Abaqus/CAE writes a part's mesh inside ``*Part`` and the named sets a user picked in the
assembly afterwards, each scoped to an instance: ``*Elset, elset=BEAM, instance=PART-1-1``. The
members are element ids *of that instance*; the assembly itself has no elements.

On the array-backed mesh path such an elset stores ids and resolves them through its parent
FEM. Adding it to the assembly's sets re-parented it to the assembly, so the next resolve looked
the ids up in a FEM with no elements: ``ValueError: The elem id "1" is not found``, and the whole
deck failed to import. The regex reader hid this by passing the keyword's own casing
(``"Elset"``) as the set type, which the id-backed path does not recognise; a deck spelling it
``*elset`` failed on the old reader too.
"""

from __future__ import annotations

import pytest

import ada

_DECK = """\
*Heading
*Part, name=PART-1
*Node
      1,          0.,          0.,          0.
      2,          1.,          0.,          0.
      3,          1.,          1.,          0.
      4,          0.,          1.,          0.
      5,          2.,          0.,          0.
      6,          2.,          1.,          0.
*Element, type=S4R
1, 1, 2, 3, 4
2, 2, 5, 6, 3
*Elset, elset=plates, generate
 1, 2, 1
** Section: Plate
*Shell Section, elset=plates, material=Steel
0.01, 5
*End Part
*Assembly, name=Assembly
*Instance, name=PART-1-1, part=PART-1
*End Instance
{keyword}, elset=PICKED, instance=PART-1-1
 2,
*Nset, nset=CORNER, instance=PART-1-1
 1,
*End Assembly
*Material, name=Steel
*Elastic
 2.1e+11, 0.3
"""


@pytest.mark.parametrize("keyword", ["*Elset", "*elset", "*ELSET"])
def test_instance_scoped_elset_resolves_against_the_instance(tmp_path, keyword):
    inp = tmp_path / "instance_sets.inp"
    inp.write_text(_DECK.format(keyword=keyword))

    a = ada.from_fem(inp, "abaqus")

    picked = a.fem.elsets["PICKED"]
    assert [el.id for el in picked.members] == [2]

    part_fem = a.parts["PART-1"].fem
    assert picked.members[0].nodes == part_fem.elements.from_id(2).nodes

    corner = a.fem.nsets["CORNER"]
    assert [n.id for n in corner.members] == [1]


def test_id_backed_set_keeps_resolving_after_moving_to_the_assembly(tmp_path):
    """The reader now builds these sets from objects, so pin the container behaviour directly:
    an id-backed elset owned by an instance still resolves once added to the assembly's sets."""
    from ada.fem import FemSet

    inp = tmp_path / "instance_sets.inp"
    inp.write_text(_DECK.format(keyword="*Elset"))
    a = ada.from_fem(inp, "abaqus")
    part_fem = a.parts["PART-1"].fem

    fs = FemSet("MOVED", [part_fem.elements.from_id(1)], "elset", parent=part_fem)
    assert fs._member_ids == [1]  # the id-backed path, which is what this test is about

    a.fem.sets.add(fs)
    a.fem.sets.link_data()

    assert [el.id for el in a.fem.elsets["MOVED"].members] == [1]
