"""Which side of a ``*Tie`` is the dependent one, proved from the deck to the BLDEP records.

Abaqus' ``*Tie`` data line is ``secondary, main`` -- the **dependent** surface first. The reader
put the first surface into ``m_set`` and the Abaqus writer wrote ``m_set, s_set`` back out, so the
two mistakes cancelled and the Abaqus round trip agreed with itself. Nothing in the suite could
see it, and every *other* consumer of the constraint read the sides the wrong way round: the Sesam
writer's ``coupling_records`` and ``shell2solid_records`` both take ``m_set`` for the independent
side, so a tie converted on that basis came out with the dependent and independent sides exchanged
-- a model that still runs, still analyses, and is wrong.

So the direction is pinned at four places, not one:

1. the deck's first surface becomes the constraint's ``s_set``;
2. the Abaqus writer puts ``s_set`` first;
3. the two together round-trip *and* land on the original deck's own names -- which a round trip
   on its own cannot tell you, since a reader and a writer that are both mirrored agree perfectly;
4. the direction reaches the Sesam deck: the secondary surface's nodes are the BLDEP slaves.

(4) is the point of the whole exercise, and it is what a test of (1) to (3) alone would miss.
"""

from __future__ import annotations

import ada
from ada.fem import Constraint
from ada.fem.formats.abaqus.write.write_constraints import constraint_str
from ada.fem.formats.sesam.write.write_constraints import coupling_records

# Two plates, a node set and a nodal surface each. ``main_surf`` is named SECOND on the *Tie data
# line, so it is the main (independent) surface; ``sec_surf`` is named first, so it is dependent.
# Constraints are read from the assembly section, so the deck is a *Part / *Assembly one.
_DECK = "\n".join(
    [
        "*Part, name=p",
        "*Node",
        "1, 0., 0., 0.",
        "2, 1., 0., 0.",
        "3, 1., 1., 0.",
        "4, 0., 1., 0.",
        "11, 0., 0., 0.1",
        "12, 1., 0., 0.1",
        "13, 1., 1., 0.1",
        "14, 0., 1., 0.1",
        "*Element, type=S4, elset=main_el",
        "1, 1, 2, 3, 4",
        "*Element, type=S4, elset=sec_el",
        "2, 11, 12, 13, 14",
        "*Nset, nset=main_n",
        "1, 2, 3, 4",
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
        "*Surface, type=NODE, name=main_surf",
        "p-1.main_n, 1.",
        "*Surface, type=NODE, name=sec_surf",
        "p-1.sec_n, 1.",
        "** Constraint: t1",
        "*Tie, name=t1, adjust=yes, position tolerance=0.2",
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


def _read(tmp_path, name: str = "tie.inp") -> ada.Assembly:
    path = tmp_path / name
    path.write_text(_DECK)
    return ada.from_fem(path, "abaqus")


def _tie(a: ada.Assembly) -> Constraint:
    ties = [c for p in a.get_all_parts_in_assembly(True) for c in p.fem.constraints.values() if c.type == "tie"]
    assert len(ties) == 1, f"expected exactly one tie, got {len(ties)}"
    return ties[0]


def test_the_first_surface_is_the_secondary(tmp_path):
    """``sec_surf, main_surf`` -> ``s_set=sec_surf``, ``m_set=main_surf``."""
    tie = _tie(_read(tmp_path))
    assert tie.s_set.name == "sec_surf", "the deck's FIRST surface is the dependent one"
    assert tie.m_set.name == "main_surf", "the deck's SECOND surface is the independent one"
    assert tie.pos_tol == 0.2 and tie.metadata["adjust"] == "yes"


def test_the_writer_puts_the_dependent_surface_first(tmp_path):
    """Whatever the model says, the data line is ``s_set, m_set``."""
    tie = _tie(_read(tmp_path))
    lines = [ln.strip() for ln in constraint_str(tie, True).splitlines() if ln.strip() and not ln.startswith("*")]
    assert lines[-1] == "sec_surf, main_surf", f"expected the secondary first, got {lines[-1]!r}"


def test_the_round_trip_keeps_the_decks_own_direction(tmp_path):
    """Read, write, read: the sides are the deck's, not mirrored.

    A round trip on its own proves only that the reader and the writer agree with each other --
    which they did while both were wrong. What makes this a direction test is that the *original
    deck's* names are asserted on the far side, so flipping reader and writer together no longer
    passes it.
    """
    first = _tie(_read(tmp_path))
    out = tmp_path / "rt"
    _read(tmp_path, "again.inp").to_fem(
        "rt", fem_format="abaqus", scratch_dir=out, overwrite=True, write_input_files_only=True
    )
    again = _tie(ada.from_fem(next(out.rglob("rt.inp")), "abaqus"))
    assert (again.s_set.name, again.m_set.name) == ("sec_surf", "main_surf")
    assert (first.s_set.name, first.m_set.name) == (again.s_set.name, again.m_set.name)


def test_the_secondary_nodes_are_the_bldep_slaves(tmp_path):
    """The Sesam side: BLDEP slaves are the secondary surface's nodes, masters the main's.

    ``coupling_records`` is the rigid-link form a tie's two sides go through, and it takes
    ``m_set`` for the master. With the sides the old way round these two assertions swap over,
    which is exactly the deck that still runs and is still wrong.
    """
    tie = _tie(_read(tmp_path))
    records = coupling_records(Constraint("t1", Constraint.TYPES.COUPLING, tie.m_set, tie.s_set, parent=tie.parent))
    assert {r.slave for r in records} == {11, 12, 13, 14}, "the secondary surface's nodes must be the slaves"
    assert {r.master for r in records} <= {1, 2, 3, 4}, "the main surface's nodes must be the masters"


def test_a_tie_parameter_that_narrows_the_secondary_region_is_reported(tmp_path):
    """``tied nset`` and ``cyclic symmetry`` are read into the metadata and acted on nowhere.

    Both of them narrow *which* secondary nodes the tie constrains, and every writer constrains
    the whole secondary region. Keeping them in the metadata and saying nothing left
    ``tied nset=only34`` producing a tie on every node of the surface, with nothing to show for
    it -- so the gap is named per tie.
    """
    from ada.fem.formats import conversion_report

    path = tmp_path / "narrow.inp"
    path.write_text(
        _DECK.replace(
            "*Tie, name=t1, adjust=yes, position tolerance=0.2",
            "*Nset, nset=only34\np-1.sec_n,\n*Tie, name=t1, adjust=yes, tied nset=only34, cyclic symmetry=cs",
        )
    )
    with conversion_report.collect() as report:
        ada.from_fem(path, "abaqus")
    reasons = {f.reason for f in report.findings if f.keyword == "*TIE" and f.subject == "t1"}
    assert any("tied nset" in r for r in reasons), reasons
    assert any("cyclic symmetry" in r for r in reasons), reasons
    assert all(f.kind == "omitted" for f in report.findings if f.keyword == "*TIE")
