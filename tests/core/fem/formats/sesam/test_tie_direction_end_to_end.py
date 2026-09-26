"""A ``*Tie`` from a deck to its BLDEP records: the dependent side stays the dependent side.

``tests/core/fem/formats/abaqus/read/test_tie_sides.py`` pins the reader and the Abaqus writer, and
``test_write_tie.py`` pins the Sesam writer against hand-built constraints. Neither of them runs the
whole chain, and the whole chain is where the defect lived: the reader put the deck's *first*
surface (Abaqus' **secondary**) into ``m_set``, the Sesam writer takes ``m_set`` for the
**independent** side, and the result was a deck with the two sides of every tie exchanged. It still
runs and it still analyses.

So this reads a real deck, hands the model to the real ``bldep_records``, and asserts on the BLDEP
records themselves: the nodes that become *dependent* are the ones the deck named first.

The deck's main surface is element-based, so this also covers the interpolated path -- a dependent
node reaches several independent nodes, and every one of them must be on the main side.
"""

from __future__ import annotations

import ada
from ada.fem.formats import conversion_report
from ada.fem.formats.sesam.write.write_constraints import bldep_records
from ada.fem.formats.sesam.write.writer import node_dofs

#: Two shell plates, 1 mm apart. ``MAIN`` is a 2x1 mesh (nodes 1-6, elements 1-2) and is named
#: SECOND on the *Tie data line, so it is Abaqus' main surface. ``SEC`` is a single element over it
#: (nodes 11-14, element 3), named first, so it is the secondary one. The ids are far enough apart
#: that "which side is which" cannot be confused with "which ids are lower".
_MAIN_NODES = {1: (0, 0, 0), 2: (1, 0, 0), 3: (2, 0, 0), 4: (0, 1, 0), 5: (1, 1, 0), 6: (2, 1, 0)}
_SEC_NODES = {11: (0.25, 0.25, 0.001), 12: (0.75, 0.25, 0.001), 13: (0.75, 0.75, 0.001), 14: (0.25, 0.75, 0.001)}

_DECK = "\n".join(
    [
        "*Part, name=p",
        "*Node",
        *[f"{nid}, {x!r}, {y!r}, {z!r}" for nid, (x, y, z) in {**_MAIN_NODES, **_SEC_NODES}.items()],
        "*Element, type=S4, elset=main_el",
        "1, 1, 2, 5, 4",
        "2, 2, 3, 6, 5",
        "*Element, type=S4, elset=sec_el",
        "3, 11, 12, 13, 14",
        "*Nset, nset=sec_n",
        "11, 12, 13, 14",
        "*Shell Section, elset=main_el, material=steel",
        "0.01, 5",
        "*Shell Section, elset=sec_el, material=steel",
        "0.01, 5",
        "*End Part",
        "*Assembly, name=a",
        "*Instance, name=p-1, part=p",
        "*End Instance",
        "*Elset, elset=main_el_a, instance=p-1",
        "1, 2",
        "*Surface, type=ELEMENT, name=main_surf",
        "main_el_a, SPOS",
        "*Surface, type=NODE, name=sec_surf",
        "p-1.sec_n, 1.",
        "** Constraint: t1",
        "*Tie, name=t1, adjust=yes, position tolerance=0.1",
        "sec_surf, main_surf",
        "*End Assembly",
        "*Material, name=steel",
        "*Elastic",
        "2.1e11, 0.3",
        "*Density",
        "7850.,",
        "",
    ]
)


def _records(tmp_path):
    path = tmp_path / "tie.inp"
    path.write_text(_DECK)
    a = ada.from_fem(path, "abaqus")
    fem = a.fem
    (tie,) = [c for c in fem.constraints.values() if c.type == "tie"]
    with conversion_report.collect() as report:
        records = bldep_records(fem, node_dofs(fem))
    return tie, records, report


def test_the_decks_first_surface_is_the_one_that_becomes_dependent(tmp_path):
    tie, records, _ = _records(tmp_path)

    assert (tie.s_set.name, tie.m_set.name) == ("sec_surf", "main_surf")
    assert records, "a tie is written, not omitted"
    assert {r.slave for r in records} == set(_SEC_NODES), "the deck's FIRST surface holds the dependent nodes"
    assert {r.master for r in records} <= set(_MAIN_NODES), "every independent node is on the deck's SECOND surface"
    # No node is both: a BLDEP whose master is itself dependent is not a relation Sesam promises
    # to resolve, and with the sides exchanged every record would be one.
    assert not ({r.slave for r in records} & {r.master for r in records})


def test_a_dependent_node_reaches_several_main_nodes_because_the_main_surface_has_facets(tmp_path):
    """The interpolated path: an element main surface gives each dependent node the facet's nodes,
    not one nearest node. Node 11 sits inside element 1, so it depends on that quad's four nodes."""
    _, records, report = _records(tmp_path)

    per_slave = {}
    for r in records:
        per_slave.setdefault(r.slave, set()).add(r.master)
    assert per_slave[11] == {1, 2, 5, 4}, "the facet node 11 projects onto is element 1"
    assert all(len(masters) > 1 for masters in per_slave.values()), "an interpolation names more than one master"

    # The weights of one facet sum to 1: that is what makes a rigid-body motion of the main surface
    # pass through exactly, so it is asserted on the records rather than taken on trust.
    for slave, masters in per_slave.items():
        total = sum(beta for r in records if r.slave == slave for s, m, beta in r.terms if s == 1 and m == 1)
        assert abs(total - 1.0) < 1e-12, f"node {slave}: translation weights sum to {total}, not 1"

    (note,) = [f for f in report.findings if f.keyword == "*TIE" and f.kind == "note"]
    assert note.details["n_tied"] == len(_SEC_NODES)
    assert note.details["cutoff"] == 0.1, "the declared position tolerance is used as it stands"
