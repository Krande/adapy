"""BNBCD companions for BLDEP, and the retained (supernode) DOFs.

A BLDEP record is only half a statement: the manual (printed 6-27) requires the same
DOFs to appear on BNBCD records, "as linear dependent (3) for the dependent node, and as
retained (4) for the independent node". ada wrote no BNBCD records for its couplings at
all, and the obvious reading of that sentence — code 4 on every master — is what turned
743 internal coupling masters of this project's model into unconnected supernodes in
Presel: code 4 *is* the definition of a supernode (printed 6-30).

DNV's own GeniE V9.2-01 settles it empirically. In a 128k-element T100.FEM with 169
BLDEP records, all 52 master nodes carry an explicit ``0 0 0 0 0 0`` record and none of
them carries a 4; the 58 nodes that do carry 4 are the assembly interface and are
neither masters nor dependents. So: dependents get 3, masters get an explicit free
record, and code 4 comes from the retained set only — either the node set named in
``metadata["sesam_retained_dofs"]`` or, with no such key, a node set named
``SESAM_SUPERNODES`` (``write_bcs.SUPERNODE_SET_NAME``), which is what lets a plain
"read an INP, write a FEM" conversion produce a usable superelement with no extra
arguments. The explicit key overrides that convention outright: naming one set must not
silently retain another as well.

These tests pin that split, the precedence between the two declarations, the conflict
errors, and the fact that the BLDEP text itself did not move when the two writers started
sharing one record type.
"""

from __future__ import annotations

import contextlib
import logging

import pytest

import ada
from ada.fem import Bc, Constraint, FemSet, Surface
from ada.fem.common import LinDep
from ada.fem.formats.sesam.read import cards
from ada.fem.formats.sesam.write.write_bcs import (
    RETAINED_KEY,
    SUPERNODE_SET_NAME,
    bnbcd_str,
    retained_dofs,
    retained_dofs_from_metadata,
)
from ada.fem.formats.sesam.write.write_constraints import (
    BldepRecord,
    bldep_records,
    constraint_str,
)
from ada.fem.formats.sesam.write.write_utils import write_ff


@contextlib.contextmanager
def _captured_logs(level=logging.WARNING):
    """Records logged by adapy's own logger at ``level`` or above.

    ``configure_logger`` turns propagation off, so ``caplog`` -- which listens on the root
    -- never sees them; this attaches to the ``ada`` logger directly instead. Same idiom as
    ``tests/core/cadit/dexpi/test_read_proteus.py``.
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


def _shell2solid_fem(with_super=False, extra_sets=None):
    """A shell edge (nodes 1, 2) meeting a solid face (nodes 11-14, offset in z).

    ``with_super`` adds an uninvolved node set "Super" (nodes 21, 22) to stand in for a
    real assembly interface. ``extra_sets`` is ``{set name: (node id, ...)}`` and adds
    further uninvolved node sets, each node placed on its own along +x so nothing
    coincides; ids must stay clear of 1, 2, 11-14 and 21, 22.
    """
    shell_nodes = [ada.Node((0, 0, 0), 1), ada.Node((1, 0, 0), 2)]
    solid_nodes = [
        ada.Node((0, 0, -0.5), 11),
        ada.Node((0, 0, 0.5), 12),
        ada.Node((1, 0, -0.5), 13),
        ada.Node((1, 0, 0.5), 14),
    ]
    extra = [ada.Node((5, 0, 0), 21), ada.Node((6, 0, 0), 22)] if with_super else []
    named = {name: [ada.Node((10.0 + nid, 0, 0), nid) for nid in ids] for name, ids in (extra_sets or {}).items()}
    named_nodes = [n for nodes in named.values() for n in nodes]

    fem = ada.FEM("MyFem", nodes=ada.api.containers.Nodes(shell_nodes + solid_nodes + extra + named_nodes))
    edge = fem.add_set(FemSet("edge", shell_nodes, "nset"))
    face = fem.add_set(FemSet("face", solid_nodes, "nset"))
    if with_super:
        fem.add_set(FemSet("Super", extra, "nset"))
    for name, nodes in named.items():
        fem.add_set(FemSet(name, nodes, "nset"))
    m = Surface("edge_surf", Surface.TYPES.NODE, edge, parent=fem)
    s = Surface("face_surf", Surface.TYPES.NODE, face, parent=fem)
    fem.add_constraint(Constraint("s2s", Constraint.TYPES.SHELL2SOLID, m, s, parent=fem))
    return fem


def _bnbcd_codes(text: str) -> dict[int, list[int]]:
    """{node id: six FIX codes} parsed back out of a BNBCD block."""
    out = {}
    for m in cards.re_bnbcd.finditer(text):
        d = m.groupdict()
        codes = [int(float(x)) for x in d["content"].split()]
        assert int(float(d["ndof"])) == 6 and len(codes) == 6
        out[int(float(d["nodeno"]))] = codes
    return out


def _bldep_slaves_masters(text: str) -> tuple[dict[int, list[int]], dict[int, list[int]]]:
    """({slave: dependent dofs}, {master: master dofs}) parsed out of a BLDEP block."""
    slaves, masters = {}, {}
    for m in cards.re_bldep.finditer(text):
        d = m.groupdict()
        fields = d["bulk"].split()
        terms = [tuple(float(x) for x in fields[i : i + 4]) for i in range(0, len(fields), 4)]
        slave, master = int(float(d["slave"])), int(float(d["master"]))
        slaves.setdefault(slave, []).extend(sorted({int(t[0]) for t in terms}))
        masters.setdefault(master, []).extend(sorted({int(t[1]) for t in terms}))
    return (
        {k: sorted(set(v)) for k, v in slaves.items()},
        {k: sorted(set(v)) for k, v in masters.items()},
    )


def test_plain_bc_keeps_todays_bnbcd_layout():
    """No constraints in play: the record layout a Bc produces is byte-for-byte what the
    old ``bc_str`` wrote, one record per node."""
    nodes = [ada.Node((0, 0, 0), 1), ada.Node((1, 0, 0), 2)]
    fem = ada.FEM("MyFem", nodes=ada.api.containers.Nodes(nodes))
    fem.add_bc(Bc("fix", fem.add_set(FemSet("support", nodes, "nset")), [1, 2, 3]))

    out = bnbcd_str([fem])
    expected = "".join(write_ff("BNBCD", [(nid, 6, 1, 1), (1, 0, 0, 0)]) for nid in (1, 2))
    assert out == expected


def test_bldep_dependents_get_code_3():
    fem = _shell2solid_fem()
    codes = _bnbcd_codes(bnbcd_str([fem], bldep_records(fem)))
    for nid in (11, 12, 13, 14):
        assert codes[nid] == [3, 3, 3, 0, 0, 0], "only the three translations are dependent"


def test_bldep_masters_get_explicit_free_record_never_code_4():
    """The defect this whole module exists for: a master is listed, and listed as free."""
    fem = _shell2solid_fem()
    text = bnbcd_str([fem], bldep_records(fem))
    codes = _bnbcd_codes(text)

    assert codes[1] == [0] * 6 and codes[2] == [0] * 6
    assert 4 not in {c for node in codes.values() for c in node}
    assert len(codes) == 6, "4 dependents + 2 masters, nothing else"


def test_retained_metadata_marks_only_the_named_set():
    fem = _shell2solid_fem(with_super=True)
    retained = retained_dofs_from_metadata([fem], {RETAINED_KEY: {"Super": [1, 2, 3, 4, 5, 6]}})
    codes = _bnbcd_codes(bnbcd_str([fem], bldep_records(fem), retained))

    assert codes[21] == [4] * 6 and codes[22] == [4] * 6
    assert codes[1] == [0] * 6 and codes[2] == [0] * 6, "masters stay free"
    assert {nid for nid, c in codes.items() if 4 in c} == {21, 22}


def test_retained_partial_dofs():
    fem = _shell2solid_fem(with_super=True)
    retained = retained_dofs_from_metadata([fem], {RETAINED_KEY: {"Super": [1, 2, 3]}})
    codes = _bnbcd_codes(bnbcd_str([fem], bldep_records(fem), retained))
    assert codes[21] == [4, 4, 4, 0, 0, 0]


def test_retained_set_name_is_case_insensitive():
    """Sesam set names are upper-cased in decks; an Abaqus-derived set keeps its own case."""
    fem = _shell2solid_fem(with_super=True)
    assert retained_dofs_from_metadata([fem], {RETAINED_KEY: {"SUPER": [1]}}) == {21: (1,), 22: (1,)}


def test_retained_set_resolves_against_the_second_fem():
    """The converter may hold the interface set on the assembly FEM rather than the part."""
    part_fem = _shell2solid_fem()
    other = ada.FEM("Assembly", nodes=ada.api.containers.Nodes([ada.Node((9, 0, 0), 31)]))
    other.add_set(FemSet("Super", list(other.nodes), "nset"))
    assert retained_dofs_from_metadata([part_fem, other], {RETAINED_KEY: {"Super": [1, 6]}}) == {31: (1, 6)}


def test_fixed_and_dependent_dof_raises():
    fem = _shell2solid_fem()
    fem.add_bc(Bc("fix", fem.add_set(FemSet("clamp", [fem.nodes.from_id(11)], "nset")), [1]))
    with pytest.raises(ValueError, match="fixed and linearly dependent"):
        bnbcd_str([fem], bldep_records(fem))


def test_retained_dependent_raises():
    fem = _shell2solid_fem()
    fem.add_set(FemSet("Super", [fem.nodes.from_id(11)], "nset"))
    retained = retained_dofs_from_metadata([fem], {RETAINED_KEY: {"Super": [1]}})
    with pytest.raises(ValueError, match="cannot be retained"):
        bnbcd_str([fem], bldep_records(fem), retained)


def test_retained_fixed_raises():
    nodes = [ada.Node((0, 0, 0), 1)]
    fem = ada.FEM("MyFem", nodes=ada.api.containers.Nodes(nodes))
    fem.add_bc(Bc("fix", fem.add_set(FemSet("clamp", nodes, "nset")), [3]))
    fem.add_set(FemSet("Super", nodes, "nset"))
    retained = retained_dofs_from_metadata([fem], {RETAINED_KEY: {"Super": [3]}})
    with pytest.raises(ValueError, match="fixed and cannot be retained"):
        bnbcd_str([fem], (), retained)


@pytest.mark.parametrize(
    "spec, match",
    [
        ({"Nope": [1]}, "is not found"),
        ({"Super": [0]}, "not an int in 1..6"),
        ({"Super": [7]}, "not an int in 1..6"),
        ({"Super": ["1"]}, "not an int in 1..6"),
        ({"Super": []}, "no dofs given"),
        ({"Super": 3}, "must be a list of ints"),
        ({"an_elset": [1]}, "is an element set"),
        ({"Empty": [1]}, "is empty"),
    ],
)
def test_unknown_set_bad_dofs_and_elset_raise(spec, match):
    fem = _shell2solid_fem(with_super=True)
    el = ada.fem.Elem(1, [fem.nodes.from_id(1), fem.nodes.from_id(2)], "LINE", parent=fem)
    fem.elements = ada.fem.containers.FemElements([el], fem_obj=fem)
    fem.add_set(FemSet("an_elset", [el], "elset"))
    fem.add_set(FemSet("Empty", [], "nset"))

    with pytest.raises(ValueError, match=match):
        retained_dofs_from_metadata([fem], {RETAINED_KEY: spec})


def test_metadata_without_the_key_is_no_retained_dofs():
    fem = _shell2solid_fem(with_super=True)
    assert retained_dofs_from_metadata([fem], None) == {}
    assert retained_dofs_from_metadata([fem], {"control_file": None}) == {}
    with pytest.raises(ValueError, match="must be a dict"):
        retained_dofs_from_metadata([fem], {RETAINED_KEY: ["Super"]})


# --- the SESAM_SUPERNODES convention, and its precedence against the explicit key ------


def test_the_reserved_set_name_is_sesam_not_sestra():
    """Sesam is the suite and this file format; Sestra is the solver. One spelling, shared
    by the writer, the docs and these tests."""
    assert SUPERNODE_SET_NAME == "SESAM_SUPERNODES"


def test_convention_set_retains_all_six_dofs_without_metadata():
    """The headline case: no metadata argument at all, and the interface set still lands."""
    fem = _shell2solid_fem(extra_sets={SUPERNODE_SET_NAME: (31, 32)})
    assert retained_dofs([fem]) == {31: (1, 2, 3, 4, 5, 6), 32: (1, 2, 3, 4, 5, 6)}

    codes = _bnbcd_codes(bnbcd_str([fem], bldep_records(fem), retained_dofs([fem])))
    assert codes[31] == [4] * 6 and codes[32] == [4] * 6
    assert {nid for nid, c in codes.items() if 4 in c} == {31, 32}, "nothing else is retained"
    assert codes[1] == [0] * 6 and codes[2] == [0] * 6, "BLDEP masters stay free"


@pytest.mark.parametrize("set_name", ["SESAM_SUPERNODES", "sesam_supernodes", "Sesam_SuperNodes"])
def test_convention_set_name_matches_case_insensitively(set_name):
    """An Abaqus-derived set keeps whatever case the INP used."""
    fem = _shell2solid_fem(extra_sets={set_name: (31,)})
    assert retained_dofs([fem]) == {31: (1, 2, 3, 4, 5, 6)}


def test_convention_resolves_a_set_on_the_assembly_fem():
    """``to_fem`` hands over [part.fem, assembly.fem]; the set may sit on either."""
    part_fem = _shell2solid_fem()
    other = ada.FEM("Assembly", nodes=ada.api.containers.Nodes([ada.Node((9, 0, 0), 41)]))
    other.add_set(FemSet(SUPERNODE_SET_NAME, list(other.nodes), "nset"))

    assert retained_dofs([part_fem, other]) == {41: (1, 2, 3, 4, 5, 6)}
    assert retained_dofs([part_fem]) == {}, "and only when it is actually there"


def test_explicit_metadata_overrides_the_convention_without_merging():
    """Explicit wins outright. A caller who names "Super" must not also get the
    SESAM_SUPERNODES set retained behind their back."""
    fem = _shell2solid_fem(with_super=True, extra_sets={SUPERNODE_SET_NAME: (31, 32)})
    resolved = retained_dofs([fem], {RETAINED_KEY: {"Super": [1, 2, 3, 4, 5, 6]}})

    assert set(resolved) == {21, 22}, "the convention set is not added on top"
    codes = _bnbcd_codes(bnbcd_str([fem], bldep_records(fem), resolved))
    assert {nid for nid, c in codes.items() if 4 in c} == {21, 22}
    assert 31 not in codes and 32 not in codes


def test_explicit_metadata_may_retain_a_subset_of_the_convention_set():
    """Naming the reserved set explicitly is how you retain fewer than six DOFs of it:
    the declared subset is used, not the convention's all-six."""
    fem = _shell2solid_fem(extra_sets={SUPERNODE_SET_NAME: (31,)})
    assert retained_dofs([fem], {RETAINED_KEY: {SUPERNODE_SET_NAME: [1, 2, 3]}}) == {31: (1, 2, 3)}

    codes = _bnbcd_codes(bnbcd_str([fem], (), retained_dofs([fem], {RETAINED_KEY: {"sesam_supernodes": [3]}})))
    assert codes[31] == [0, 0, 4, 0, 0, 0]


def test_empty_metadata_dict_is_an_explicit_retain_nothing():
    """The key being present at all disables the convention, so ``{}`` opts out."""
    fem = _shell2solid_fem(extra_sets={SUPERNODE_SET_NAME: (31,)})
    assert retained_dofs([fem], {RETAINED_KEY: {}}) == {}
    assert bnbcd_str([fem], (), retained_dofs([fem], {RETAINED_KEY: {}})) == ""


def test_metadata_holding_only_other_keys_still_uses_the_convention():
    """``to_fem`` always inserts "control_file", so an absent retained key has to mean
    absent, not "metadata was given"."""
    fem = _shell2solid_fem(extra_sets={SUPERNODE_SET_NAME: (31,)})
    assert retained_dofs([fem], {"control_file": None}) == {31: (1, 2, 3, 4, 5, 6)}
    assert retained_dofs([fem], {RETAINED_KEY: None}) == {31: (1, 2, 3, 4, 5, 6)}


def test_no_metadata_and_no_convention_set_retains_nothing_silently():
    """Today's behaviour for every existing model: no code 4, and no noise about it."""
    fem = _shell2solid_fem(with_super=True)
    with _captured_logs(logging.INFO) as records:
        resolved = retained_dofs([fem])
    assert resolved == {}
    assert not [r for r in records if "supernode" in r.getMessage().lower()]

    codes = _bnbcd_codes(bnbcd_str([fem], bldep_records(fem), resolved))
    assert 4 not in {c for node in codes.values() for c in node}


def test_convention_logs_the_set_name_and_node_count_at_info():
    """A conversion that retained nothing and one that retained 58 nodes have to be
    distinguishable in a log."""
    fem = _shell2solid_fem(extra_sets={SUPERNODE_SET_NAME: (31, 32, 33)})
    with _captured_logs(logging.INFO) as records:
        retained_dofs([fem])

    messages = [r.getMessage() for r in records if r.levelno == logging.INFO]
    assert any(SUPERNODE_SET_NAME in m and "3 nodes" in m for m in messages), messages


def test_convention_warns_when_the_reserved_set_is_empty_or_an_elset():
    """A near-miss is reported rather than silently doing nothing, but it is not an error:
    the convention is an offer, so a malformed one must not break a conversion."""
    fem = _shell2solid_fem(extra_sets={SUPERNODE_SET_NAME: ()})
    with _captured_logs() as records:
        assert retained_dofs([fem]) == {}
    assert any("empty" in r.getMessage() for r in records)

    el_fem = _shell2solid_fem()
    el = ada.fem.Elem(1, [el_fem.nodes.from_id(1), el_fem.nodes.from_id(2)], "LINE", parent=el_fem)
    el_fem.elements = ada.fem.containers.FemElements([el], fem_obj=el_fem)
    el_fem.add_set(FemSet(SUPERNODE_SET_NAME, [el], "elset"))
    with _captured_logs() as records:
        assert retained_dofs([el_fem]) == {}
    assert any("element set" in r.getMessage() for r in records)


def test_bldep_text_is_unchanged_by_the_record_refactor():
    """``BldepRecord.to_str`` has to reproduce the text the old inline writer produced —
    the same header and the same trailing 0.0 per term — or every existing deck moves."""
    fem = _shell2solid_fem()
    records = bldep_records(fem)
    # Only pins that constraint_str is still the join of the records it dispatches; the
    # reconstruction below is what actually compares against the pre-refactor text.
    assert constraint_str(fem) == "".join(r.to_str() for r in records)

    master, slave = fem.nodes.from_id(1), fem.nodes.from_id(11)
    legacy_rows = [(slave.id, master.id, 3, 9)]
    for rel in LinDep(master.p, slave.p).to_integer_list():
        legacy_rows.append(tuple(list(rel) + [0.0]))
    legacy = write_ff("BLDEP", legacy_rows)

    first = next(r for r in records if r.slave == 11)
    assert first.to_str() == legacy
    assert (len(first.slave_dofs), len(first.terms)) == (3, 9)
    assert first.slave_dofs == (1, 2, 3) and first.master_dofs == (1, 2, 3, 4, 5, 6)

    parsed = next(cards.re_bldep.finditer(first.to_str())).groupdict()
    assert (int(float(parsed["nddof"])), int(float(parsed["ndep"]))) == (3, 9)
    fields = parsed["bulk"].split()
    assert len(fields) == 4 * 9
    terms = [tuple(float(x) for x in fields[i : i + 4]) for i in range(0, len(fields), 4)]
    assert all(t[3] == 0.0 for t in terms), "the 4th field of every term row is 0.0"
    # slave dof 1 (x) picks up master dof 5 (Ry) with beta = dz = -0.5
    assert next(t[2] for t in terms if (t[0], t[1]) == (1, 5)) == -0.5


def test_no_constraints_no_bcs_writes_no_bnbcd():
    fem = ada.FEM("MyFem", nodes=ada.api.containers.Nodes([ada.Node((0, 0, 0), 1)]))
    assert bnbcd_str([fem]) == ""


def test_two_bcs_on_one_node_merge_into_one_record():
    """The old writer emitted one record per Bc, so a node in two BCs was declared
    twice with contradictory codes."""
    nodes = [ada.Node((0, 0, 0), 1)]
    fem = ada.FEM("MyFem", nodes=ada.api.containers.Nodes(nodes))
    fem.add_bc(Bc("t", fem.add_set(FemSet("t_set", nodes, "nset")), [1, 2, 3]))
    fem.add_bc(Bc("r", fem.add_set(FemSet("r_set", nodes, "nset")), [4]))

    codes = _bnbcd_codes(bnbcd_str([fem]))
    assert len(codes) == 1 and codes[1] == [1, 1, 1, 1, 0, 0]


def test_bc_on_an_elset_is_skipped():
    nodes = [ada.Node((0, 0, 0), 1), ada.Node((1, 0, 0), 2)]
    fem = ada.FEM("MyFem", nodes=ada.api.containers.Nodes(nodes))
    el = ada.fem.Elem(1, nodes, "LINE", parent=fem)
    fem.elements = ada.fem.containers.FemElements([el], fem_obj=fem)
    fem.add_bc(Bc("fix", fem.add_set(FemSet("els", [el], "elset")), [1]))

    assert bnbcd_str([fem]) == ""


def test_chained_master_is_warned_not_raised():
    """A master that is itself dependent is a modelling smell, not a file error."""
    fem = ada.FEM("MyFem", nodes=ada.api.containers.Nodes([ada.Node((i, 0, 0), i) for i in (1, 2, 3)]))
    records = [
        BldepRecord(2, 1, tuple((d, d, 1.0) for d in (1, 2, 3))),
        BldepRecord(3, 2, tuple((d, d, 1.0) for d in (1, 2, 3))),
    ]
    with _captured_logs() as warnings:
        codes = _bnbcd_codes(bnbcd_str([fem], records))
    assert codes[2] == [3, 3, 3, 0, 0, 0], "the dependency wins over the free master record"
    assert codes[1] == [0] * 6 and codes[3] == [3, 3, 3, 0, 0, 0]
    messages = " ".join(r.getMessage() for r in warnings)
    assert "master" in messages and "dependent" in messages, "the chain is reported, not silent"


def _plate_deck_with_coupling(tmp_path, use_metadata=True):
    """A meshed plate with a COUPLING and a separate retained node set, written out.

    The retained set is named ``SESAM_SUPERNODES`` either way. With ``use_metadata`` it is
    also declared explicitly; without, ``to_fem`` gets no ``metadata`` argument at all and
    the convention has to find it. Both must produce the same code-4 nodes.
    """
    pl = ada.Plate("pl", [(0, 0), (2, 0), (2, 1), (0, 1)], 0.01)
    p = ada.Part("p") / pl
    a = ada.Assembly("a") / p
    p.fem = p.to_fem_obj(0.4, "shell")

    nodes = sorted(p.fem.nodes, key=lambda n: n.id)
    assert len(nodes) >= 6
    master, slaves, supers = nodes[0], nodes[1:4], nodes[4:6]

    m_set = p.fem.add_set(FemSet("ref", [master], "nset"))
    s_set = p.fem.add_set(FemSet("region", slaves, "nset"))
    p.fem.add_set(FemSet(SUPERNODE_SET_NAME, supers, "nset"))
    p.fem.add_constraint(Constraint("cpl", Constraint.TYPES.COUPLING, m_set, s_set, parent=p.fem))

    kwargs = {"metadata": {RETAINED_KEY: {SUPERNODE_SET_NAME.lower(): [1, 2, 3, 4, 5, 6]}}} if use_metadata else {}
    a.to_fem("m", fem_format="sesam", scratch_dir=tmp_path, overwrite=True, **kwargs)
    deck = (tmp_path / "m" / "mT1.FEM").read_text()
    return deck, master.id, [n.id for n in slaves], [n.id for n in supers]


@pytest.mark.parametrize("use_metadata", [True, False], ids=["explicit_metadata", "supernode_set_convention"])
def test_to_fem_writes_bnbcd_before_bldep_with_retained_and_units(tmp_path, use_metadata):
    deck, master_id, slave_ids, super_ids = _plate_deck_with_coupling(tmp_path, use_metadata)

    assert deck.count("UNITS") == 1, "UNITS stays; Presel only warns about it, GeniE writes it too"
    lines = deck.splitlines()
    first = {
        flag: next(i for i, ln in enumerate(lines) if ln.startswith(flag)) for flag in ("BNBCD", "BLDEP", "GELMNT1")
    }
    assert first["BNBCD"] < first["BLDEP"] < first["GELMNT1"], "GeniE's record order"

    codes = _bnbcd_codes(deck)
    slaves, masters = _bldep_slaves_masters(deck)

    assert sorted(slaves) == sorted(slave_ids)
    assert sorted(masters) == [master_id]
    for nid, dofs in slaves.items():
        assert all(codes[nid][d - 1] == 3 for d in dofs)
    for nid, dofs in masters.items():
        assert all(codes[nid][d - 1] == 0 for d in dofs), "a master is free, not retained"
    assert {nid for nid, c in codes.items() if 4 in c} == set(super_ids)
    assert all(codes[nid] == [4] * 6 for nid in super_ids)
    assert len(codes) == len(slave_ids) + 1 + len(super_ids)
