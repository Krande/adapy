"""The Sesam reader and BNBCD FIX code 4 — the retained (supernode) DOFs.

The writer learned to emit code 4 (``write_bcs``), either from
``metadata["sesam_retained_dofs"]`` or from a node set named ``SESAM_SUPERNODES``. The
reader threw it away: ``grab_bc`` skipped codes 0 and 4 alike, so a record of
``4 4 4 4 4 4`` came back as a ``Bc`` with an *empty* dof list plus a junk one-node set
``bc<id>_set``. Two defects in one: the superelement's external interface — the thing
Presel matches against the assembly — was silently lost on every Sesam -> ada -> Sesam
round trip, and every supernode gained a meaningless ``Bc`` (58 of them in this project's
deck, thousands in a large model).

These tests pin the fix: code 4 comes back as the ``SESAM_SUPERNODES`` node set, which is
exactly the name the writer's convention picks up, so the round trip closes with no caller
action; a record holding only codes 0 and 4 produces no ``Bc`` and no set; and codes
1/2/3 and constraint-attached nodes behave exactly as before.
"""

from __future__ import annotations

import contextlib
import logging

import pytest

import ada
from ada.config import Config
from ada.fem import Bc, Constraint, FemSet
from ada.fem.formats.sesam.read import cards
from ada.fem.formats.sesam.read.read_constraints import get_bcs
from ada.fem.formats.sesam.write.write_bcs import (
    SUPERNODE_SET_NAME,
    bnbcd_str,
    retained_dofs,
)
from ada.fem.formats.sesam.write.write_utils import write_ff


@contextlib.contextmanager
def _captured_logs(level=logging.WARNING):
    """Records logged by adapy's own logger at ``level`` or above.

    ``configure_logger`` turns propagation off, so ``caplog`` — which listens on the root —
    never sees them; this attaches to the ``ada`` logger directly instead.
    """
    records: list[logging.LogRecord] = []

    class _Collector(logging.Handler):
        def emit(self, record):
            records.append(record)

    logger = logging.getLogger("ada")
    handler = _Collector(level=level)
    logger.addHandler(handler)
    previous = logger.level
    logger.setLevel(level)
    try:
        yield records
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous)


# --- hand-written BNBCD fixtures -------------------------------------------------------


def _fem_with_nodes(*node_ids: int) -> ada.FEM:
    nodes = [ada.Node((float(i), 0, 0), nid) for i, nid in enumerate(node_ids)]
    return ada.FEM("MyFem", nodes=ada.api.containers.Nodes(nodes))


def _bnbcd(node_id: int, codes) -> str:
    """One BNBCD record, written the way the writer writes it so the reader's own regex
    matches it. NDOF is len(codes), as the manual requires."""
    codes = list(codes)
    return write_ff("BNBCD", [(node_id, len(codes), codes[0], codes[1]), tuple(codes[2:])])


def _supernode_set(fem: ada.FEM) -> FemSet | None:
    try:
        return fem.sets.get_nset_from_name(SUPERNODE_SET_NAME)
    except ValueError:
        return None


def test_all_six_retained_gives_the_supernode_set_and_no_bc():
    fem = _fem_with_nodes(7)
    bcs = get_bcs(_bnbcd(7, [4] * 6), fem)

    assert bcs == [], "code 4 is not a boundary condition"
    fem_set = _supernode_set(fem)
    assert fem_set is not None and [n.id for n in fem_set.members] == [7]
    assert fem_set.type == "nset"


def test_a_retained_node_leaves_no_empty_bc_or_junk_set_behind():
    """The old reader built ``bc7_set`` and an empty ``Bc`` before deciding anything."""
    fem = _fem_with_nodes(7)
    get_bcs(_bnbcd(7, [4] * 6), fem)

    assert [s.name for s in fem.sets if s.name.startswith("bc")] == []
    # ``.bc`` is set by grab_bc alone; Node does not carry the attribute otherwise.
    assert getattr(fem.nodes.from_id(7), "bc", None) is None


def test_an_all_free_record_yields_nothing_at_all():
    """``0 0 0 0 0 0`` is what the writer emits for a BLDEP master. It is not a BC, and it
    is not a supernode either — so no ``Bc``, no set, and no SESAM_SUPERNODES set."""
    fem = _fem_with_nodes(7)
    assert get_bcs(_bnbcd(7, [0] * 6), fem) == []
    assert _supernode_set(fem) is None
    assert list(fem.sets) == []


def test_a_genuine_bc_still_reads_its_dofs():
    fem = _fem_with_nodes(7)
    bcs = get_bcs(_bnbcd(7, [1, 1, 1, 0, 0, 0]), fem)

    assert len(bcs) == 1
    assert bcs[0].dofs == [1, 2, 3]
    assert [n.id for n in bcs[0].fem_set.members] == [7]
    assert bcs[0].fem_set.name == "bc7_set"
    assert fem.nodes.from_id(7).bc is bcs[0]
    assert _supernode_set(fem) is None, "a fixed dof is not a retained dof"


def test_linear_dependency_code_3_still_reads_as_an_ordinary_dof():
    """Out of scope to change here: code 3 is a BLDEP companion, but with no BLDEP record
    in the bulk there is no constraint to suppress the Bc, and it lands as a dof."""
    fem = _fem_with_nodes(7)
    bcs = get_bcs(_bnbcd(7, [3, 3, 3, 0, 0, 0]), fem)
    assert len(bcs) == 1 and bcs[0].dofs == [1, 2, 3]


def test_a_constraint_attached_node_still_returns_none():
    fem = _fem_with_nodes(1, 2)
    m_set = fem.add_set(FemSet("m", [fem.nodes.from_id(1)], "nset"))
    s_set = fem.add_set(FemSet("s", [fem.nodes.from_id(2)], "nset"))
    fem.add_constraint(Constraint("cpl", Constraint.TYPES.COUPLING, m_set, s_set, parent=fem))

    assert get_bcs(_bnbcd(2, [1] * 6), fem) == [], "BLDEP owns the dependent node"
    assert [s.name for s in fem.sets] == ["m", "s"]


def test_a_constraint_attached_node_still_keeps_its_retained_flag():
    """Retained and dependent are statements about different dofs of one node. The reader
    never emits a ``Bc`` for a constraint node, but the interface flag is not the Bc's to
    lose — so it is collected before that early return."""
    fem = _fem_with_nodes(1, 2)
    m_set = fem.add_set(FemSet("m", [fem.nodes.from_id(1)], "nset"))
    s_set = fem.add_set(FemSet("s", [fem.nodes.from_id(2)], "nset"))
    fem.add_constraint(Constraint("cpl", Constraint.TYPES.COUPLING, m_set, s_set, parent=fem))

    assert get_bcs(_bnbcd(2, [3, 3, 3, 4, 4, 4]), fem) == []
    fem_set = _supernode_set(fem)
    assert fem_set is not None and [n.id for n in fem_set.members] == [2]


def test_a_partial_retained_pattern_still_joins_the_set_and_warns():
    """``4 4 4 0 0 0``: the SESAM_SUPERNODES convention retains all six dofs of every
    member, so the set cannot express this. The node still goes in — losing the interface
    is the defect being fixed, and a widened pattern is still a connectable deck — but the
    widening is logged, never silent."""
    fem = _fem_with_nodes(7, 8)
    with _captured_logs() as warnings:
        bcs = get_bcs(_bnbcd(7, [4, 4, 4, 0, 0, 0]) + _bnbcd(8, [4] * 6), fem)

    assert bcs == []
    fem_set = _supernode_set(fem)
    assert fem_set is not None and sorted(n.id for n in fem_set.members) == [7, 8]

    messages = " ".join(r.getMessage() for r in warnings)
    assert "node 7" in messages and "[1, 2, 3]" in messages, messages
    assert "1 of 2" in messages, messages
    assert "node 8" not in messages, "the all-six node is not the one that lost detail"


def test_no_warning_when_every_retained_node_carries_all_six_dofs():
    fem = _fem_with_nodes(7, 8)
    with _captured_logs() as warnings:
        get_bcs(_bnbcd(7, [4] * 6) + _bnbcd(8, [4] * 6), fem)
    assert [r.getMessage() for r in warnings] == []


def test_a_bc_and_a_retained_dof_on_one_node_are_both_read():
    """Representable on read: ``1 1 1 4 4 4`` gives a Bc on dofs 1-3 and membership of the
    supernode set. Not round-trippable through the convention, though — see the next
    test."""
    fem = _fem_with_nodes(7)
    bcs = get_bcs(_bnbcd(7, [1, 1, 1, 4, 4, 4]), fem)

    assert len(bcs) == 1 and bcs[0].dofs == [1, 2, 3]
    fem_set = _supernode_set(fem)
    assert fem_set is not None and [n.id for n in fem_set.members] == [7]


def test_a_bc_and_a_retained_dof_on_one_node_cannot_be_written_back():
    """Documented limit, not a reader defect: the convention retains *all six* dofs of a
    member, so dof 1 of node 7 would be both fixed and retained, and the writer refuses
    that rather than picking a winner. A caller with such a deck has to name the dofs
    explicitly via ``metadata["sesam_retained_dofs"]``."""
    fem = _fem_with_nodes(7)
    bcs = get_bcs(_bnbcd(7, [1, 1, 1, 4, 4, 4]), fem)
    fem.add_bc(bcs[0])

    with pytest.raises(ValueError, match="fixed and cannot be retained"):
        bnbcd_str([fem], retained=retained_dofs([fem]))


def test_the_reader_reuses_the_writers_set_name_constant():
    """If the writer ever renames the convention, the reader must follow in one edit."""
    fem = _fem_with_nodes(7)
    get_bcs(_bnbcd(7, [4] * 6), fem)
    assert [s.name for s in fem.sets] == [SUPERNODE_SET_NAME]


# --- deck level: the round trip that was one-way ---------------------------------------


@pytest.fixture(params=[True, False], ids=["streaming", "object"])
def both_reader_paths(request, monkeypatch):
    """Sesam .FEM has two readers, picked by ``Config().meshing_array_backed``. Both run
    ``get_bcs``, so both have to keep the interface."""
    monkeypatch.setattr(Config(), "meshing_array_backed", request.param)
    return request.param


def _plate_deck(tmp_path, name="m"):
    """A meshed plate with a ``SESAM_SUPERNODES`` node set and a real support, written out
    by ``to_fem`` with no ``metadata`` at all — the convention has to find the set."""
    pl = ada.Plate("pl", [(0, 0), (2, 0), (2, 1), (0, 1)], 0.01)
    p = ada.Part("p") / pl
    a = ada.Assembly("a") / p
    p.fem = p.to_fem_obj(0.4, "shell")

    nodes = sorted(p.fem.nodes, key=lambda n: n.id)
    assert len(nodes) >= 6
    supers, fixed = nodes[:3], nodes[3:5]
    p.fem.add_set(FemSet(SUPERNODE_SET_NAME, supers, "nset"))
    p.fem.add_bc(Bc("support", p.fem.add_set(FemSet("support_set", fixed, "nset")), [1, 2, 3]))

    a.to_fem(name, fem_format="sesam", scratch_dir=tmp_path, overwrite=True)
    return tmp_path / name / f"{name}T1.FEM", [n.id for n in supers], [n.id for n in fixed]


def _code_4_nodes(deck_text: str) -> set[int]:
    out = set()
    for m in cards.re_bnbcd.finditer(deck_text):
        d = m.groupdict()
        if 4 in [int(float(x)) for x in d["content"].split()]:
            out.add(int(float(d["nodeno"])))
    return out


def _read_part(fem_file):
    b = ada.from_fem(fem_file)
    parts = [p for p in b.get_all_parts_in_assembly(True) if p.fem is not None and len(p.fem.nodes) > 0]
    assert len(parts) == 1
    return parts[0]


def test_reading_a_deck_brings_the_supernode_set_back(tmp_path, both_reader_paths):
    fem_file, super_ids, fixed_ids = _plate_deck(tmp_path)
    assert _code_4_nodes(fem_file.read_text()) == set(super_ids), "precondition: the deck has code 4"

    fem = _read_part(fem_file).fem
    fem_set = fem.sets.get_nset_from_name(SUPERNODE_SET_NAME)
    assert sorted(n.id for n in fem_set.members) == sorted(super_ids)


def test_reading_a_deck_creates_no_empty_bc_for_the_supernodes(tmp_path, both_reader_paths):
    fem_file, super_ids, fixed_ids = _plate_deck(tmp_path)
    fem = _read_part(fem_file).fem

    assert [bc.name for bc in fem.bcs if len(bc.dofs) == 0] == []
    bc_nodes = {n.id for bc in fem.bcs for n in bc.fem_set.members}
    assert bc_nodes == set(fixed_ids), "only the real support comes back as a Bc"
    for bc in fem.bcs:
        assert bc.dofs == [1, 2, 3]
    assert [s.name for s in fem.sets if s.name.startswith("bc") and s.name.endswith("_set")] == [
        f"bc{nid}_set" for nid in sorted(fixed_ids)
    ]


def test_write_read_write_keeps_the_same_code_4_nodes(tmp_path, both_reader_paths):
    """The defect end to end: read a Sesam deck into ada, write it back, and the
    superelement's external interface has to still be there — with no caller action, i.e.
    no ``metadata`` argument on either write."""
    first_file, super_ids, _ = _plate_deck(tmp_path, "m")
    first = _code_4_nodes(first_file.read_text())

    b = ada.from_fem(first_file)
    b.to_fem("m2", fem_format="sesam", scratch_dir=tmp_path, overwrite=True)
    second_file = tmp_path / "m2" / "m2T1.FEM"

    second = _code_4_nodes(second_file.read_text())
    assert second == first == set(super_ids)
    # and the set itself is still named so a third trip would find it again
    assert SUPERNODE_SET_NAME in second_file.read_text()
