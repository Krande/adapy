"""Multipart FEM concatenation: fold several part-instance FEMs into one so the single-part
writers (Sesam/MED/Genie) can export a multi-instance model. Ids are renumbered to avoid
conflicts and set names are prefixed with the instance name."""

import ada
from ada.fem import FemSet
from ada.fem.concat import concatenate_fem_to_single_part


def _part_with_fem(name, origin_x):
    pl = ada.Plate("pl", [(origin_x, 0), (origin_x + 1, 0), (origin_x + 1, 1), (origin_x, 1)], 0.01)
    p = ada.Part(name) / pl
    p.fem = pl.to_fem_obj(0.5, "shell")
    # A named node set referencing the first 3 nodes — same name across both parts on purpose.
    first_three = [p.fem.nodes.from_id(i) for i in sorted(n.id for n in p.fem.nodes)[:3]]
    p.fem.sets.add(FemSet("CLAMP", first_three, FemSet.TYPES.NSET, parent=p.fem))
    return p


def test_concatenate_multipart_fem():
    a = ada.Assembly("A")
    a.add_part(_part_with_fem("PartA", 0.0))
    a.add_part(_part_with_fem("PartB", 10.0))

    parts = [p for p in a.get_all_parts_in_assembly() if p.fem and len(p.fem.nodes) > 0]
    assert len(parts) == 2
    n_total = sum(len(p.fem.nodes) for p in parts)
    e_total = sum(len(p.fem.elements) for p in parts)
    # both parts independently number nodes from 1 -> ids collide pre-merge
    assert min(n.id for n in parts[0].fem.nodes) == min(n.id for n in parts[1].fem.nodes)

    base = concatenate_fem_to_single_part(a)

    # one FEM part now holds everything; the other was emptied
    nonempty = [p for p in a.get_all_parts_in_assembly() if p.fem and len(p.fem.nodes) > 0]
    assert len(nonempty) == 1 and nonempty[0] is base
    assert len(base.fem.nodes) == n_total
    assert len(base.fem.elements) == e_total

    # no duplicate node/element ids after the renumber
    nids = [n.id for n in base.fem.nodes]
    eids = [e.id for e in base.fem.elements]
    assert len(set(nids)) == len(nids)
    assert len(set(eids)) == len(eids)

    # both CLAMP sets survive, prefixed with their instance name (no merge-by-name), and each
    # resolves to 3 existing nodes.
    clamp_sets = [s for s in base.fem.sets if s.name.endswith("_CLAMP")]
    assert len(clamp_sets) == 2
    assert {s.name for s in clamp_sets} == {"PartA_CLAMP", "PartB_CLAMP"}
    for s in clamp_sets:
        members = s.members
        assert len(members) == 3
        assert all(base.fem.nodes.from_id(m.id) is not None for m in members)
