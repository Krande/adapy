"""GeniE itself as the oracle for the beam end hinges adapy reads from its XML and writes back, and for what a corrosion
addition does to GeniE's mesh.

The fixture is GeniE V9.3-00's own export of ``files/fem_files/sesam/genie_hinges_corrosion.js``. GeniE meshes it as it
is and as adapy writes it after reading it (0.5 m, ``T1.FEM``), and the two decks are compared:

* **BELFIX at every beam end that has one**, keyed by the positions of the element's two nodes: OPT and the six values
  (OPT 3: 1 connected / 0 free; OPT 4: -1 rigid or a spring's stiffness). GELREF1 names an end's BELFIX in its
  per-node FIXNO list (the record's tail, in GEONO FIXNO ECCNO TRANSNO order for those given as -1);
* **GELTH and the beam sections** (GBEAMG): the same. And measured: the corroded plate (2 mm front, 3 mm back) meshes
  to its gross 20 mm, and the corroded beam to the gross section -- GeniE keeps corrosion for its code checks, not its
  FE model, so adapy reading the plate and the beam at full size is the model GeniE meshes.
"""

from __future__ import annotations

import pathlib
import shutil

import numpy as np
import pytest
from test_genie_gxml_supports import _genie_runtime, _key, _mesh_in_genie, _records

import ada

pytestmark = pytest.mark.skipif(_genie_runtime() is None, reason="GeniE is not installed")

FIXTURE = "genie_hinges_corrosion.xml"


def _mesh(fem_path) -> dict:
    """The deck's BELFIX per hinged beam end, its GELTH thicknesses and its beam sections."""
    recs = _records(fem_path)
    coord = {int(v[0]): _key(v[1:4]) for n, v in recs if n == "GCOORD"}
    elnodes = {int(v[1]): [int(x) for x in v[4:6]] for n, v in recs if n == "GELMNT1" and int(v[2]) == 15}
    belfix = {int(v[0]): (int(v[1]), tuple(round(float(x), 6) for x in v[4:10])) for n, v in recs if n == "BELFIX"}
    ends = {}
    for n, v in recs:
        if n != "GELREF1" or int(v[0]) not in elnodes:
            continue
        head, tail = v[8:12], list(v[12:])
        if int(head[1]) != -1:  # FIXNO a single value for both nodes (0: no hinge)
            fixes = [int(head[1])] * 2
        else:
            lists = [i for i, f in enumerate(head) if int(f) == -1]
            at = lists.index(1) * 2  # this element's 2 nodes per -1 field, in field order
            fixes = [int(x) for x in tail[at : at + 2]]
        n1, n2 = (coord[i] for i in elnodes[int(v[0])])
        for node, other, fix in ((n1, n2, fixes[0]), (n2, n1, fixes[1])):
            if fix:
                ends[(node, other)] = belfix[fix]
    thicknesses = sorted(round(float(v[1]), 6) for n, v in recs if n == "GELTH")
    sections = sorted(tuple(round(float(x), 9) for x in v[2:8]) for n, v in recs if n == "GBEAMG")
    return {"ends": ends, "thicknesses": thicknesses, "sections": sections}


@pytest.fixture(scope="module")
def both_decks(tmp_path_factory):
    work = tmp_path_factory.mktemp("hinges")
    src = work / FIXTURE
    shutil.copy(pathlib.Path(__file__).resolve().parents[2] / "files" / "fem_files" / "sesam" / FIXTURE, src)
    _mesh_in_genie(src, work / "genie", "genie")
    a = ada.from_genie_xml(src)
    adapy_xml = work / "adapy" / "adapy.xml"
    adapy_xml.parent.mkdir()
    a.to_genie_xml(adapy_xml)
    _mesh_in_genie(adapy_xml, work / "adapy", "adapy")
    return _mesh(work / "genie" / "T1.FEM"), _mesh(work / "adapy" / "T1.FEM")


def test_genie_meshes_adapys_hinges_into_the_same_belfix_at_the_same_ends(both_decks):
    genie, adapy = both_decks
    assert genie["ends"] == adapy["ends"]
    # not vacuous: the rigid hinge (OPT 3, bending rotations free) and the flexible one (OPT 4, two springs) are there
    assert sorted(genie["ends"].values()) == [
        (3, (1.0, 1.0, 1.0, 1.0, 0.0, 0.0)),
        (4, (-1.0, -1.0, -1.0, -1.0, 1e6, 2e6)),
    ]
    at = {opt: node for (node, _), (opt, _) in genie["ends"].items()}
    assert np.allclose(at[3], (4, 0, 0)) and np.allclose(at[4], (4, 0, 0))  # both at the joint of Bm1 and Bm2


def test_a_corrosion_addition_leaves_genies_mesh_at_the_gross_size(both_decks):
    genie, adapy = both_decks
    assert genie["thicknesses"] == adapy["thicknesses"] == [0.02]
    # GeniE writes the corroded beam's section as a second GBEAMG (``Sec_X``) with the plain one's properties
    assert len(genie["sections"]) == 2 and len(set(genie["sections"])) == 1, "the corroded section is the plain one"
    assert set(genie["sections"]) == set(adapy["sections"])
