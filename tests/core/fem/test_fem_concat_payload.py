"""The full per-element payload has to survive the multipart FEM merge.

``concatenate_fem_to_single_part`` rebuilt each ``ElemArrayBlock`` from four things --
``conn``, ``el_ids``, ``fem_secs``, ``elsets`` -- but a block's ``__slots__`` also carry
``ecc``, ``hinge`` and ``metadata``, and the array container carries the
Mass/Spring/Connector objects whose rows sit in those blocks. None of the four survived
the merge, and the merge runs for *any* multi-part model on the way into a single-part
writer (Sesam ``writer.to_fem``, ``fem/formats/general.py``, ``fem/formats/utils.py``).

So a two-part model exported to Sesam came out with every beam back on its own axis, every
hinge fully fixed and every point mass gone -- silently, and only on the multi-part path:
GECCEN 1 -> 0, BELFIX 1 -> 0, mass 1 -> 0 against the identical single-part model. That
made ``feat(fem): write beam end eccentricities as GECCEN`` produce nothing at all there.

The assertions are on deck text rather than on object attributes wherever a record exists
for the thing, because the deck is what the defect destroyed. The comparison is always
against the *equivalent single-part model* -- the same beams, the same offsets, the same
masses in one part instead of two -- so the test states the invariant (splitting a model
across parts must not change what is exported) instead of a magic number.

The three sparse side tables are keyed by the row INSIDE the block, not by node row and
not by element id, which is the trap this merge has to get right: ``ecc``/``hinge``/
``metadata`` shift by the number of rows that ctype already holds, node references inside
them shift by ``node_off``, and a retained special's element id shifts by ``el_off``.
"""

from __future__ import annotations

import logging

import pytest

import ada
from ada.api.mesh.containers import to_array_backed
from ada.api.mesh.proxies import NodeProxy
from ada.config import logger
from ada.fem import FemSet, Mass
from ada.fem.concat import concatenate_fem_to_single_part
from ada.fem.elements import Hinge, HingeProp
from ada.fem.formats.sesam.read import cards
from ada.fem.shapes.definitions import MassTypes

#: (name, y, eccentricity) per beam. The two offsets are deliberately DIFFERENT: GECCEN is
#: deduplicated by vector, so two beams sharing one offset would write a single record and
#: the count would no longer distinguish "both carried" from "one carried".
CASES = (("A", 0.0, (0.0, 0.0, -0.3)), ("B", 4.0, (0.0, 0.25, 0.0)))

MASS_VALUE = 12.0
HINGE_DOFS = [4, 5, 6]


def _prepare(part: ada.Part, cases) -> ada.Part:
    """Mesh ``part`` to one line element per beam and hang the whole payload off it.

    One mass on each beam's first node, one ``hinge_prop``, and -- after packing -- the
    ``h1`` metadata the Sesam BELFIX block is written from. ``hinge_prop`` and the mass are
    set before ``to_array_backed`` so they travel the way a read model's do (``from_fem``
    captures ``hinge`` into the block; the mass is retained in ``_packed_specials``);
    ``metadata`` is set after, because ``from_fem`` does not capture it.
    """
    part.fem = part.to_fem_obj(5.0, "line")
    elems = sorted(part.fem.elements.lines, key=lambda e: e.id)
    assert len(elems) == len(cases), "each beam must mesh to exactly one line element"
    for (name, _, _), el in zip(cases, elems):
        n = el.nodes[0]
        fs = part.fem.add_set(FemSet(f"ms_{name}", [n], FemSet.TYPES.NSET))
        part.fem.add_mass(Mass(f"m_{name}", fs, MASS_VALUE, MassTypes.MASS, parent=part.fem))
        el.hinge_prop = HingeProp(end1=Hinge(retained_dofs=[1, 2, 3], csys=None, fem_node=n))
    to_array_backed(part.fem)
    for el in part.fem.elements.lines:
        el.metadata["h1"] = HINGE_DOFS
    return part


def _beam(name, y, ecc) -> ada.Beam:
    return ada.Beam(f"bm_{name}", (0, y, 0), (4, y, 0), "IPE300", e1=ecc, e2=ecc)


def _single_part_assembly() -> ada.Assembly:
    """Both beams in one part -- the path that works today, and the yardstick."""
    p = ada.Part("P") / [_beam(*c) for c in CASES]
    return ada.Assembly("A") / _prepare(p, CASES)


def _multi_part_assembly() -> ada.Assembly:
    """The same two beams, one per part. Ids collide before the merge, by construction."""
    a = ada.Assembly("A")
    for case in CASES:
        a.add_part(_prepare(ada.Part(case[0]) / _beam(*case), (case,)))
    return a


def _deck(assembly: ada.Assembly, tmp_path, name: str) -> str:
    assembly.to_fem(name, fem_format="sesam", scratch_dir=tmp_path, overwrite=True)
    return (tmp_path / name / f"{name}T1.FEM").read_text()


def _count(text: str, card: str) -> int:
    return sum(1 for ln in text.splitlines() if ln.startswith(card))


def _field(text: str, regex, field: str) -> list[int]:
    return [int(float(m.groupdict()[field])) for m in regex.finditer(text)]


#: GELMNT1 ELTYP of a 1-noded mass element, the form a point mass is written in.
_MASS_ELTYP = 11


def _eltyps(text: str) -> dict[int, int]:
    return {
        int(float(m.groupdict()["elno"])): int(float(m.groupdict()["eltyp"]))
        for m in cards.GELMNT1.to_ff_re().finditer(text)
    }


def _mass_nodes(text: str) -> list[int]:
    """The node of every mass element (GELMNT1 ELTYP 11)."""
    return [
        int(float(m.groupdict()["nids"].split()[0]))
        for m in cards.GELMNT1.to_ff_re().finditer(text)
        if int(float(m.groupdict()["eltyp"])) == _MASS_ELTYP
    ]


def _beam_eccnos(text: str) -> list[int]:
    eltyps = _eltyps(text)
    return [
        int(float(m.groupdict()["eccno"]))
        for m in cards.GELREF1.to_ff_re().finditer(text)
        if eltyps[int(float(m.groupdict()["elno"]))] != _MASS_ELTYP
    ]


@pytest.fixture
def decks(tmp_path) -> tuple[str, str]:
    """``(single, multi)`` deck text for the two equivalent models."""
    return (
        _deck(_single_part_assembly(), tmp_path, "single"),
        _deck(_multi_part_assembly(), tmp_path, "multi"),
    )


@pytest.fixture
def warnings_visible(monkeypatch, caplog):
    """``configure_logger`` turns propagation off, so ``caplog`` sees nothing from adapy
    unless it is switched back on."""
    monkeypatch.setattr(logger, "propagate", True)
    with caplog.at_level(logging.WARNING, logger=logger.name):
        yield caplog


# ── the payload is there before the merge ────────────────────────────────────────────


def test_the_parts_carry_the_payload_the_merge_has_to_keep():
    """Guard the fixture: if the parts stopped carrying eccentricities, hinges or masses,
    every deck comparison below would pass for the wrong reason."""
    for p in _multi_part_assembly().get_all_subparts():
        assert len(list(p.fem.elements.lines_ecc)) == 1
        assert len(list(p.fem.elements.lines_hinged)) == 1
        assert len(list(p.fem.elements.masses)) == 1
        assert [el.metadata.get("h1") for el in p.fem.elements.lines] == [HINGE_DOFS]


# ── what the deck must show ──────────────────────────────────────────────────────────


def test_geccen_count_survives_the_merge(decks):
    """The reported defect: 2 GECCEN in one part, 0 across two."""
    single, multi = decks
    assert _count(multi, "GECCEN") == _count(single, "GECCEN") == 2


def test_geccen_vectors_are_the_same_set_in_both_decks(decks):
    """Not just as many records -- the same offsets. A merge that kept a count but lost the
    node the offset belongs to would write the vectors and drop them from GELREF1."""
    single, multi = decks

    def vectors(text):
        return {
            tuple(round(float(m.groupdict()[k]), 10) for k in ("ex", "ey", "ez"))
            for m in cards.re_geccen.finditer(text)
        }

    assert vectors(multi) == vectors(single)
    assert len(vectors(multi)) == 2


def test_every_merged_element_still_references_its_eccentricity(decks):
    """``eccno`` on GELREF1 is what binds an element to its GECCEN -- a record nothing
    points at is as good as absent. Both ends of a beam here share one vector, so each
    element is written with a single positive eccno, and the two beams' differ."""
    single, multi = decks
    eccnos = _beam_eccnos(multi)
    assert eccnos == _beam_eccnos(single)
    assert len(eccnos) == len(set(eccnos)) == 2
    assert all(no > 0 for no in eccnos)


def test_belfix_count_survives_the_merge(decks):
    """Hinges ride on the block's ``metadata`` side table (``h1``/``h2``), which the merge
    dropped along with ``ecc``."""
    single, multi = decks
    assert _count(multi, "BELFIX") == _count(single, "BELFIX") == 2


def test_mass_count_survives_the_merge(decks):
    """A mass's value lives only on the ``Mass`` object beside the packed row, so a merge
    that carries blocks alone loses every mass element and its MGMASS."""
    single, multi = decks
    assert _count(multi, "MGMASS") == _count(single, "MGMASS") == 2
    assert len(_mass_nodes(multi)) == len(_mass_nodes(single)) == 2


def test_mass_records_are_identical_across_the_two_decks(decks):
    """Same nodes, same NDOF, same matrix -- the split into parts is invisible."""
    single, multi = decks

    def records(text):
        return sorted(
            (int(float(m.groupdict()["ndof"])), m.groupdict()["bulk"].split()) for m in cards.re_mgmass.finditer(text)
        )

    assert records(multi) == records(single)
    assert sorted(_mass_nodes(multi)) == sorted(_mass_nodes(single))


# ── the offsets: nothing may collide, and every reference must follow its row ─────────


def test_merged_node_and_element_ids_stay_distinct(decks):
    """What the per-part offsets exist for. Both parts number from 1 before the merge."""
    _, multi = decks
    nodes = _field(multi, cards.GNODE.to_ff_re(), "nodeno")
    elems = _field(multi, cards.GELMNT1.to_ff_re(), "elno")
    assert len(nodes) == len(set(nodes)) == 4
    assert len(elems) == len(set(elems)) == 4  # the two beams and the two mass elements


def test_the_two_masses_land_on_two_different_nodes(decks):
    """A mass on part 1 and a mass on part 2 both sat on node 1 before the merge. If the
    special's node references were not offset with everything else, both records would name
    the same node and one mass would overwrite the other."""
    _, multi = decks
    nodes = _mass_nodes(multi)
    assert len(nodes) == len(set(nodes)) == 2


def test_each_eccentricity_still_names_a_node_of_its_own_element():
    """The GECCEN tail is built by looking the eccentricity's node up among the element's
    own nodes; an un-offset node reference falls out of that list and the offset is dropped
    with a warning. Asserted on the merged model so the failure is localised here rather
    than only showing up as a missing record."""
    merged = concatenate_fem_to_single_part(_multi_part_assembly())

    elems = list(merged.fem.elements.lines_ecc)
    assert len(elems) == 2
    for el in elems:
        own = {n.id for n in el.nodes}
        for end in (el.eccentricity.end1, el.eccentricity.end2):
            assert end.node.id in own, f"element {el.id}: ecc node {end.node.id} not in {own}"


def test_each_hinge_still_names_a_node_of_its_own_element():
    """Same offset trap, for the ``hinge`` side table's ``fem_node``."""
    merged = concatenate_fem_to_single_part(_multi_part_assembly())

    elems = list(merged.fem.elements.lines_hinged)
    assert len(elems) == 2
    for el in elems:
        own = {n.id for n in el.nodes}
        assert el.hinge_prop.end1.fem_node.id in own


def test_a_merged_mass_keeps_its_row_and_its_id_together():
    """The object's element id has to move with ``el_off`` like the row it stands for, or
    the two representations of one element drift apart."""
    merged = concatenate_fem_to_single_part(_multi_part_assembly())

    masses = sorted(merged.fem.elements.masses, key=lambda m: m.id)
    assert [m.name for m in masses] == ["m_A", "m_B"]
    assert len({m.id for m in masses}) == 2
    for m in masses:
        # the id names a row in the merged store, and it is that mass's own row
        ctype, row = merged.fem.nodes.store.elem_loc(m.id)
        assert isinstance(ctype, MassTypes)
        assert [n.id for n in merged.fem.elements.from_id(m.id).nodes] == [n.id for n in m.members]


def test_merged_masses_are_rebound_to_the_merged_store():
    """Not to the source parts' stores, and not to object ``Node``s: one retained object
    node would pin a whole part's object mesh and freeze its coordinates."""
    merged = concatenate_fem_to_single_part(_multi_part_assembly())

    members = [n for m in merged.fem.elements.masses for n in m.members]
    assert len(members) == 2
    assert all(isinstance(n, NodeProxy) for n in members)

    zs = [n.p[2] for n in members]
    merged.fem.nodes.move(move=(0.0, 0.0, 10.0))
    assert [n.p[2] for n in members] == pytest.approx([z + 10.0 for z in zs])


def test_no_unbacked_special_warning_after_the_merge(warnings_visible):
    """``ArrayElements._warn_on_unbacked_special_blocks`` names this merge as the case it
    was written to catch -- mass rows with no object behind them. After the fix there are
    none, so the warning must be silent."""
    merged = concatenate_fem_to_single_part(_multi_part_assembly())

    assert len(list(merged.fem.elements.masses)) == 2
    assert "carry no Mass" not in warnings_visible.text


# ── the merge stays non-destructive ──────────────────────────────────────────────────


def test_the_merge_does_not_re_key_the_source_parts():
    """The source parts keep their FEMs in the tree, so a payload re-pointed in place
    instead of copied would leave them naming nodes they do not have."""
    a = _multi_part_assembly()
    before = [
        [(el.id, el.eccentricity.end1.node.id, dict(el.metadata)) for el in p.fem.elements.lines_ecc]
        for p in a.get_all_subparts()
    ]

    merged = concatenate_fem_to_single_part(a)
    for el in merged.fem.elements.lines:  # what a writer does to the merged model
        el.metadata["transno"] = 99

    after = [
        [(el.id, el.eccentricity.end1.node.id, dict(el.metadata)) for el in p.fem.elements.lines_ecc]
        for p in a.get_all_subparts()
    ]
    assert after == before
    assert all("transno" not in md for part in after for _, _, md in part)


def test_single_part_export_never_goes_through_the_merge(tmp_path, monkeypatch):
    """The single-part deck is the one that works today and the one a Sestra-validated deck
    rides on. Nothing in this fix may reach it -- and nothing does, because a single-part
    assembly never calls the merge at all."""
    import ada.fem.concat as concat_mod

    def boom(*args, **kwargs):
        raise AssertionError("the single-part path must not call concatenate_fem_to_single_part")

    monkeypatch.setattr(concat_mod, "concatenate_fem_to_single_part", boom)

    text = _deck(_single_part_assembly(), tmp_path, "single")
    assert (_count(text, "GECCEN"), _count(text, "BELFIX"), _count(text, "MGMASS")) == (2, 2, 2)


# ── the other home for a special: added, not packed ───────────────────────────────────


def test_masses_added_after_packing_are_carried_too(tmp_path):
    """A mass handed to ``add()`` lands in ``_overflow`` and has no block row at all -- the
    Sesam reader's own masses arrive that way. The block merge saw neither list, so those
    masses were dropped just as completely, and their ids have to be covered by the same
    per-part offset or they collide with the next part's elements."""

    def part(case):
        p = ada.Part(case[0]) / _beam(*case)
        p.fem = p.to_fem_obj(5.0, "line")
        to_array_backed(p.fem)
        el = next(iter(p.fem.elements.lines))
        fs = p.fem.add_set(FemSet(f"ms_{case[0]}", [el.nodes[0]], FemSet.TYPES.NSET))
        p.fem.add_mass(Mass(f"m_{case[0]}", fs, MASS_VALUE, MassTypes.MASS, parent=p.fem))
        return p

    a = ada.Assembly("A")
    for case in CASES:
        a.add_part(part(case))

    merged = concatenate_fem_to_single_part(a)
    masses = list(merged.fem.elements.masses)
    assert sorted(m.name for m in masses) == ["m_A", "m_B"]
    assert len({m.id for m in masses}) == 2
    # no mass id may shadow a structural element id
    assert {m.id for m in masses}.isdisjoint({el.id for el in merged.fem.elements.lines})

    text = _deck(ada.Assembly("W") / merged, tmp_path, "overflow")
    nodes = _mass_nodes(text)
    assert len(nodes) == len(set(nodes)) == 2
