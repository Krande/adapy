"""GELMNT1 / GELREF1 record order and the GELREF1 ``transno`` field.

Two defects that together stopped an Abaqus-derived superelement from loading in
Sesam Presel:

* **Record order.** The array-backed mesh iterates block by block (one block per
  element type), so a mixed shell/solid/beam model emitted its element records with
  the internal numbers jumping back and forth. The Sesam input interface file
  description requires GELMNT1 (printed 6-65) and GELREF1 (printed 6-68) in
  increasing internal element number ELNO; Presel stops at the first step backwards
  with ``ELEMENT n INTERNAL ALREADY EXISTS``. The writer sorts, it does not
  renumber -- the element ids the model carries have to survive into the deck.

* **GUNIVEC scope.** GUNIVEC (printed 6-92) is a beam construct: Sesam element
  types 2, 15 and 23 only. The writer used to stamp one on every sectioned element
  and copy its number into the shells' and solids' GELREF1 ``transno``, which
  Presel rejected with ``TRANSFORMATION NUMBER 1 DOES NOT EXSIST``. Non-beams now
  get ``transno`` 0 -- default element axes, printed 6-67 -- and a ``transno`` a
  caller placed on a non-beam element itself (a BNTRCOS reference) is passed
  through untouched.
"""

from __future__ import annotations

import logging

import numpy as np
import pytest

import ada
from ada.config import logger
from ada.fem import FemSection, FemSet
from ada.fem.formats.sesam.node_order import SESAM_ORDER
from ada.fem.formats.sesam.read import cards
from ada.fem.formats.sesam.write.write_elements import elem_str, write_elem
from ada.fem.formats.sesam.write.writer import univec_str
from ada.fem.shapes import ElemType
from ada.fem.shapes.definitions import LineShapes, ShellShapes, SolidShapes
from ada.fem.shapes.node_order import NATIVE_MIDSIDE_EDGES
from ada.materials.metals import CarbonSteel

THICK_MAP = {0.01: 1}


def _tet10_coords(origin) -> np.ndarray:
    """One TETRA10 in adapy native ordering: four corners then the six mid-sides."""
    corners = np.array([[0.0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]]) + np.asarray(origin, dtype=float)
    coords = np.vstack([corners, np.zeros((6, 3))])
    for slot, (i, j) in NATIVE_MIDSIDE_EDGES[SolidShapes.TETRA10].items():
        coords[slot] = 0.5 * (corners[i] + corners[j])
    return coords


def _bind_sections(fem, store, blocks_with_secs) -> None:
    """Give every row of the listed blocks a FemSection, ids fixed so the deck is stable."""
    mat = ada.Material("S355", CarbonSteel("S355"), parent=fem)
    mat.id = 1
    sec = ada.Section("MySec", "IG", h=0.3, w_top=0.2, w_btn=0.2, t_w=0.01, t_fbtn=0.01, t_ftop=0.01)
    sec.id = 1
    secs = {
        ElemType.SOLID: FemSection("solid_sec", ElemType.SOLID, FemSet("solids", [], "elset", parent=fem), mat),
        ElemType.SHELL: FemSection(
            "shell_sec", ElemType.SHELL, FemSet("shells", [], "elset", parent=fem), mat, thickness=0.01
        ),
        ElemType.LINE: FemSection(
            "line_sec",
            ElemType.LINE,
            FemSet("lines", [], "elset", parent=fem),
            mat,
            section=sec,
            # A beam's local_z is a real orientation request -- this is the one
            # element family GUNIVEC is defined for.
            local_z=(0.0, 0.0, 1.0),
        ),
    }
    for ctype, sec_type in blocks_with_secs:
        blk = store.blocks[ctype]
        blk.fem_secs = [secs[sec_type]] * len(blk)


def _mixed_array_fem(tet_ids=(2, 5), quad_ids=(1, 4), line_ids=(3,)):
    """An array-backed FEM whose blocks hold *interleaved* element ids.

    The blocks are added solids-first, so the container's own iteration order is
    ``[2, 5, 1, 4, 3]`` -- exactly the pattern that made Presel reject the deck.
    Node ids are disjoint per element, so which row an element was written with is
    readable straight off its GELMNT1 record (see A-T2).
    """
    from ada.api.mesh.containers import ArrayElements, ArrayNodes
    from ada.api.mesh.store import MeshArrays

    n_tet, n_quad, n_line = len(tet_ids), len(quad_ids), len(line_ids)
    coords = [_tet10_coords((3.0 * i, 0, 0)) for i in range(n_tet)]
    coords += [np.array([[0.0, 3, 0], [1, 3, 0], [1, 4, 0], [0, 4, 0]]) + (3.0 * i, 0, 0) for i in range(n_quad)]
    coords += [np.array([[0.0, 6, 0], [1, 6, 0]]) + (3.0 * i, 0, 0) for i in range(n_line)]
    coords = np.vstack(coords)

    node_ids = np.arange(1, coords.shape[0] + 1, dtype=np.int64)
    store = MeshArrays(coords, node_ids)

    nxt = 1

    def take(n_elem, n_per_elem):
        nonlocal nxt
        ids = np.arange(nxt, nxt + n_elem * n_per_elem, dtype=np.int64)
        nxt += n_elem * n_per_elem
        return ids.reshape(n_elem, n_per_elem)

    store.add_elem_block_from_id_conn(SolidShapes.TETRA10, np.asarray(tet_ids), take(n_tet, 10))
    store.add_elem_block_from_id_conn(ShellShapes.QUAD, np.asarray(quad_ids), take(n_quad, 4))
    if n_line:
        store.add_elem_block_from_id_conn(LineShapes.LINE, np.asarray(line_ids), take(n_line, 2))

    fem = ada.FEM("MyFem")
    fem.nodes = ArrayNodes(store, parent=fem)
    fem.elements = ArrayElements(store, fem_obj=fem)

    blocks = [(SolidShapes.TETRA10, ElemType.SOLID), (ShellShapes.QUAD, ElemType.SHELL)]
    if n_line:
        blocks.append((LineShapes.LINE, ElemType.LINE))
    _bind_sections(fem, store, blocks)
    return fem


def _field(match, name) -> int:
    return int(float(match.groupdict()[name]))


def _gelmnt1(text) -> dict[int, tuple[int, list[int]]]:
    """``{elno: (eltyp, node ids)}`` for every GELMNT1 record, in file order."""
    out = {}
    for m in cards.GELMNT1.to_ff_re().finditer(text):
        d = m.groupdict()
        out[_field(m, "elno")] = (_field(m, "eltyp"), [int(float(x)) for x in d["nids"].split()])
    return out


def _gelmnt1_ids(text) -> list[int]:
    return [_field(m, "elno") for m in cards.GELMNT1.to_ff_re().finditer(text)]


def _gelref1_transno(text) -> dict[int, int]:
    """``{elno: transno}`` for every GELREF1 record, in file order."""
    return {_field(m, "elno"): _field(m, "transno") for m in cards.GELREF1.to_ff_re().finditer(text)}


def _gelref1_ids(text) -> list[int]:
    return [_field(m, "elno") for m in cards.GELREF1.to_ff_re().finditer(text)]


def test_gelmnt1_and_gelref1_are_emitted_in_increasing_element_id():
    fem = _mixed_array_fem()
    assert [el.id for el in fem.elements.stru_elements] == [2, 5, 1, 4, 3], "fixture must interleave"

    text = elem_str(fem, THICK_MAP)
    assert _gelmnt1_ids(text) == [1, 2, 3, 4, 5]
    assert _gelref1_ids(text) == [1, 2, 3, 4, 5]


def test_sorted_elements_keep_their_own_block_row():
    """Sorting the proxy list must not let an element read another row's connectivity.

    The ``_row`` lookup is keyed on the element's own block type, so tet 2 keeps its
    own nodes even though it is written in quad 1's former position. Node ids are
    disjoint per element, so a crossed lookup would be unmistakable.
    """
    fem = _mixed_array_fem()
    store = fem.elements.store
    expected = {}
    for ctype in (SolidShapes.TETRA10, ShellShapes.QUAD, LineShapes.LINE):
        blk = store.blocks[ctype]
        perm_ids = store.node_ids[SESAM_ORDER.conn_to_format(ctype, blk.conn)]
        for row, eid in enumerate(blk.el_ids.tolist()):
            expected[eid] = perm_ids[row].tolist()

    records = _gelmnt1(elem_str(fem, THICK_MAP))
    assert {eid: nids for eid, (_, nids) in records.items()} == expected
    # and the permutation is still applied: a native-ordered tet would read 1..10
    assert records[2][1] != list(range(1, 11))


def test_duplicate_element_ids_raise():
    fem = _mixed_array_fem(tet_ids=(2, 4), quad_ids=(1, 4))
    with pytest.raises(ValueError, match="Doubly defined element id"):
        elem_str(fem, THICK_MAP)


def test_gunivec_only_for_beams_and_transno_zero_elsewhere():
    fem = _mixed_array_fem()
    uni = univec_str(fem)
    assert uni.count("GUNIVEC") == 1  # the single line element, and nothing else

    by_type = {el.id: el.type for el in fem.elements.stru_elements}
    for el in fem.elements.stru_elements:
        # only the beam was handed a transno; the shells and solids were left alone
        assert ("transno" in el.metadata) is isinstance(el.type, LineShapes), f"element {el.id} ({el.type})"

    transnos = _gelref1_transno(elem_str(fem, THICK_MAP))
    assert transnos == {eid: (1 if isinstance(by_type[eid], LineShapes) else 0) for eid in by_type}


def test_explicit_transno_on_non_beam_is_passed_through():
    """A BNTRCOS reference a caller put on a shell is the documented hook, so it stays."""
    fem = _mixed_array_fem()
    univec_str(fem)
    shell = next(el for el in fem.elements.shell)
    shell.metadata["transno"] = 7
    assert _field(next(cards.GELREF1.to_ff_re().finditer(write_elem(shell, THICK_MAP))), "transno") == 7


def test_plate_deck_has_no_gunivec_and_keeps_units(tmp_path):
    """A shell-only deck references no transformation at all -- and still has UNITS.

    UNITS stays: Presel warns ``UNKNOWN IDENTIFIER: UNITS`` on GeniE's own files too,
    so it is noise, not the defect.
    """
    pl = ada.Plate("pl", [(0, 0), (1, 0), (1, 1), (0, 1)], 0.01)
    p = ada.Part("p") / pl
    a = ada.Assembly("a") / p
    p.fem = p.to_fem_obj(0.4, "shell")
    n_shells = len(list(p.fem.elements.shell))
    assert n_shells > 1

    a.to_fem("m", fem_format="sesam", scratch_dir=tmp_path, overwrite=True)
    deck = tmp_path / "m" / "mT1.FEM"
    text = deck.read_text()

    assert "GUNIVEC" not in text
    assert set(_gelref1_transno(text).values()) == {0}
    assert text.count("UNITS") == 1

    b = ada.from_fem(deck)
    read_back = sum(len(list(part.fem.elements.shell)) for part in b.get_all_parts_in_assembly(True))
    assert read_back == n_shells


def test_beam_deck_keeps_one_gunivec(tmp_path):
    """The beam side is untouched: one GUNIVEC per distinct local_z, referenced by the beam."""
    bm = ada.Beam("bm", (0, 0, 0), (4, 0, 0), "IPE300")
    p = ada.Part("p") / bm
    a = ada.Assembly("a") / p
    # A mesh size longer than the beam keeps it at a single line element.
    p.fem = p.to_fem_obj(5.0, "line")
    a.to_fem("m", fem_format="sesam", scratch_dir=tmp_path, overwrite=True)
    text = (tmp_path / "m" / "mT1.FEM").read_text()

    assert text.count("GUNIVEC") == 1
    transnos = _gelref1_transno(text)
    assert len(transnos) == len(list(p.fem.elements.lines))
    assert set(transnos.values()) == {1}


def test_object_path_elem_gen_is_also_sorted():
    """The object mesh path shares the sort.

    ``FemElements.__init__`` sorts what it is constructed with, so a list passed to the
    constructor would be in order before the writer ever saw it and would pin nothing.
    ``add()`` appends, so building the container empty and adding 7, 3, 11, 1 is what
    actually hands ``elem_gen`` an unsorted container.
    """
    from ada.api.containers import Nodes
    from ada.fem import Elem
    from ada.fem.containers import FemElements

    nodes = [ada.Node((i, 0, 0), i + 1) for i in range(4)]
    fem = ada.FEM("MyFem", nodes=Nodes(nodes), elements=FemElements([]))
    elems = [Elem(eid, [nodes[0], nodes[1], nodes[2], nodes[3]], ShellShapes.QUAD) for eid in (7, 3, 11, 1)]
    for el in elems:
        fem.elements.add(el)
    assert [el.id for el in fem.elements] == [7, 3, 11, 1]  # unsorted going in

    elset = fem.add_set(FemSet("shells", elems, "elset"))
    mat = ada.Material("S355", CarbonSteel("S355"), parent=fem)
    mat.id = 1
    fem_sec = FemSection("shell_sec", ElemType.SHELL, elset, mat, thickness=0.01)
    for el in elems:
        el.fem_sec = fem_sec

    text = elem_str(fem, THICK_MAP)
    assert _gelmnt1_ids(text) == [1, 3, 7, 11]
    assert _gelref1_ids(text) == [1, 3, 7, 11]
    assert set(_gelref1_transno(text).values()) == {0}


def test_beam_in_the_container_overflow_still_gets_a_gunivec():
    """A beam added to the array container with ``add()`` lands in ``_overflow``, outside
    the mesh blocks. ``elem_gen`` writes it -- it iterates ``stru_elements``, which covers
    the overflow -- so ``univec_str`` has to reach it too. Taking the container's ``lines``
    view instead would walk the blocks only and emit that beam with TRANSNO 0, quietly
    giving it default axes instead of the local_z its section asks for."""
    from ada.fem import Elem

    fem = _mixed_array_fem(line_ids=())  # blocks: solids and shells only
    line_sec = FemSection(
        "overflow_line_sec",
        ElemType.LINE,
        FemSet("overflow_lines", [], "elset", parent=fem),
        ada.Material("S355", CarbonSteel("S355"), parent=fem),
        section=ada.Section("MySec2", "IG", h=0.3, w_top=0.2, w_btn=0.2, t_w=0.01, t_fbtn=0.01, t_ftop=0.01),
        local_z=(0.0, 1.0, 0.0),
    )
    line_sec.material.id = 1
    line_sec.section.id = 1
    nodes = list(fem.nodes)[:2]
    beam = fem.elements.add(Elem(99, nodes, LineShapes.LINE, fem_sec=line_sec, parent=fem))

    assert beam not in list(fem.elements.lines)  # the gap this guards against
    assert beam.id in [el.id for el in fem.elements.stru_elements]

    assert univec_str(fem).count("GUNIVEC") == 1
    assert beam.metadata["transno"] == 1
    assert _gelref1_transno(elem_str(fem, THICK_MAP))[99] == 1


# --------------------------------------------------------- internal numbering gaps
#
# Third defect on the same records: ``elem_gen`` skips CONNECTORs, unsectioned elements
# and springs (each already logged), and because GELMNT1/GELREF1 carry the model's own
# element ids as Sesam internal element numbers, every skip leaves a hole in a numbering
# Sesam expects to run 1..N. Renumbering is out -- the id correspondence is the point --
# so the writer says what the skip did to the deck, in one message alongside the count of
# skipped elements, rather than leaving the reader to connect the two.


@pytest.fixture
def warnings_visible(monkeypatch, caplog):
    """``configure_logger`` turns propagation off, so ``caplog`` -- which listens on the
    root logger -- sees nothing from adapy unless propagation is switched back on."""
    monkeypatch.setattr(logger, "propagate", True)
    with caplog.at_level(logging.WARNING, logger=logger.name):
        yield caplog


def _quad_fem(ids, unsectioned=()):
    """Object-path FEM of one quad per id; those in ``unsectioned`` get no FemSection
    (written with MATNO = GEONO = 0)."""
    from ada.api.containers import Nodes
    from ada.fem import Elem
    from ada.fem.containers import FemElements

    nodes = [ada.Node((i, 0, 0), i + 1) for i in range(4)]
    fem = ada.FEM("MyFem", nodes=Nodes(nodes), elements=FemElements([]))
    elems = [Elem(eid, nodes, ShellShapes.QUAD) for eid in ids]
    for el in elems:
        fem.elements.add(el)

    sectioned = [el for el in elems if el.id not in unsectioned]
    elset = fem.add_set(FemSet("shells", sectioned, "elset"))
    mat = ada.Material("S355", CarbonSteel("S355"), parent=fem)
    mat.id = 1
    fem_sec = FemSection("shell_sec", ElemType.SHELL, elset, mat, thickness=0.01)
    for el in sectioned:
        el.fem_sec = fem_sec
    return fem


def test_missing_ids_counts_every_gap_and_lists_the_first_few():
    from ada.fem.formats.sesam.write.write_elements import _missing_ids

    assert _missing_ids([1, 2, 3], 5) == ([], 0)
    assert _missing_ids([], 5) == ([], 0)
    assert _missing_ids([1, 2, 4], 5) == ([3], 1)
    assert _missing_ids([4], 5) == ([1, 2, 3], 3)
    # the total does not depend on how many examples were collected
    examples, n_missing = _missing_ids([20], 3)
    assert (examples, n_missing) == ([1, 2, 3], 19)


def test_skipping_an_element_warns_that_internal_numbering_has_a_gap(warnings_visible):
    """One message carries both facts: N skipped, and the numbering is no longer 1..N.

    A connector is what the writer skips (an unsectioned element used to be skipped too, and
    is now written with no material or geometry -- see the next test)."""
    from ada.fem import Connector, ConnectorSection

    fem = _quad_fem((1, 2, 4))
    n = fem.nodes.from_id
    fem.elements.add(Connector("con", 3, n(1), n(2), "BUSHING", ConnectorSection("csec", parent=fem), parent=fem))
    text = elem_str(fem, THICK_MAP)
    assert _gelmnt1_ids(text) == [1, 2, 4]  # the skipped id is simply absent

    gap_warnings = [r.getMessage() for r in warnings_visible.records if "not contiguous" in r.getMessage()]
    assert len(gap_warnings) == 1, gap_warnings
    msg = gap_warnings[0]
    assert "1 element number(s) are missing" in msg
    assert "first: 3" in msg
    assert "1 element(s) were skipped" in msg


def test_an_unsectioned_element_is_written_with_no_material_or_geometry():
    fem = _quad_fem((1, 2, 3), unsectioned=(2,))
    text = elem_str(fem, THICK_MAP)
    assert _gelmnt1_ids(text) == [1, 2, 3]
    refs = {int(float(m["elno"])): m for m in (x.groupdict() for x in cards.GELREF1.to_ff_re().finditer(text))}
    assert (int(float(refs[2]["matno"])), int(float(refs[2]["geono"]))) == (0, 0)
    assert int(float(refs[1]["matno"])) == 1


def test_a_contiguous_deck_does_not_warn(warnings_visible):
    fem = _quad_fem((1, 2, 3, 4))
    assert _gelmnt1_ids(elem_str(fem, THICK_MAP)) == [1, 2, 3, 4]
    assert not [r for r in warnings_visible.records if "not contiguous" in r.getMessage()]


def test_ids_that_never_started_at_one_are_reported_too(warnings_visible):
    """Sesam's internal numbering is expected to start at 1, so a deck of ids 10..12 is
    just as much a gap as one with a hole in the middle -- and the writer still does not
    renumber."""
    fem = _quad_fem((10, 11, 12))
    assert _gelmnt1_ids(elem_str(fem, THICK_MAP)) == [10, 11, 12]

    (msg,) = [r.getMessage() for r in warnings_visible.records if "not contiguous" in r.getMessage()]
    assert "9 element number(s) are missing" in msg
    assert "first: 1, 2, 3, 4, 5, ..." in msg
