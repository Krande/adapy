"""Regression: array-backed Sesam read renumbers nodes/elements from internal to
external ids at the end, but the sets are id-backed (they captured the *internal* ids
at read time). The renumber must propagate to the sets, otherwise every set member
resolves to a now-missing id.

This reproduced as ``ValueError: The elem id "787" is not found`` when exporting
a jacket Sesam FEM (its elset members referenced internal ids that had been renumbered to
external ones). The object path stays correct for free because its sets hold Node/Elem
objects whose ``.id`` is renumbered in place; only the array path needed the explicit
remap.
"""

import ada
from ada.api.mesh.containers import ArrayElements, to_array_backed
from ada.fem import FemSet
from ada.fem.formats.sesam.read.reader import _remap_id_backed_sets


def _shell_fem():
    pl = ada.Plate("pl", [(0, 0), (2, 0), (2, 1.5), (0, 1.5)], 0.02)
    p = ada.Part("p") / pl
    p.fem = pl.to_fem_obj(0.5, "shell")
    return p.fem


def test_id_backed_sets_follow_element_renumber():
    fem = _shell_fem()
    picked = [e.id for e in fem.elements][:3]
    fem.sets.add(FemSet("myset", [fem.elements.from_id(i) for i in picked], FemSet.TYPES.ELSET, parent=fem))

    # Flip to the array substrate — this is what the Sesam array reader builds; it also
    # converts the set to id-backed (capturing the current/"internal" ids).
    to_array_backed(fem)
    assert isinstance(fem.elements, ArrayElements)

    # Renumber every element +1000 (stands in for the internal->external ext_map) and
    # propagate to the id-backed sets exactly as the reader now does.
    emap = {e.id: e.id + 1000 for e in fem.elements}
    fem.elements.renumber(renumber_map=emap)
    _remap_id_backed_sets(fem, node_map={}, elem_map=emap)

    members = fem.sets.get_elset_from_name("myset").members  # would raise "elem id ... not found" pre-fix
    assert sorted(m.id for m in members) == sorted(emap[i] for i in picked)


def test_id_backed_nsets_follow_node_renumber():
    fem = _shell_fem()
    picked = [n.id for n in fem.nodes][:4]
    fem.sets.add(FemSet("nset", [fem.nodes.from_id(i) for i in picked], FemSet.TYPES.NSET, parent=fem))

    to_array_backed(fem)
    nmap = {n.id: n.id + 5000 for n in fem.nodes}
    fem.nodes.renumber(renumber_map=nmap)
    _remap_id_backed_sets(fem, node_map=nmap, elem_map={})

    members = fem.sets.get_nset_from_name("nset").members
    assert sorted(m.id for m in members) == sorted(nmap[i] for i in picked)
