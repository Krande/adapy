"""One BLDEP term per ``(slave_dof, master_dof)``, however many constraints declared it.

``_merged`` puts one record on each ``(slave, master)`` pair because the manual (BLDEP, section
7.2.14) says "The same combination of SLAVE and MASTER may occur only once". It concatenated the
terms of every record it merged, so the *term* list could hold the same ``(slave_dof,
master_dof)`` twice -- and Sestra adds the betas of a repeated pair together. A deck declaring
``u(1,1) = 1.0 * u(2,1)`` twice therefore converted to ``u(1,1) = 2.0 * u(2,1)``: the dependency
doubled, with nothing in the written deck to show it had been meant only once.

The two ways a pair repeats are not the same fault and are not treated the same:

* the same beta twice is a duplicate declaration of one dependency -- the same ``*Equation``
  pasted twice, say. Written once; nothing is lost, so it is a note.
* two different betas are two incompatible statements about one dof -- a ``*Tie`` and an
  ``*Equation`` over the same node pair. Neither summing them nor averaging them is what the
  deck says, so the later one is refused by name and the first-declared relation is written.
"""

from __future__ import annotations

import ada
from ada.fem import Constraint, FemSet
from ada.fem.formats import conversion_report
from ada.fem.formats.sesam.write.write_constraints import bldep_records


def _two_node_fem(*coefs: float) -> ada.FEM:
    """Nodes 1 and 2, with one ``*Equation``-style constraint per coefficient in ``coefs``.

    Each is ``1.0 * u(1,1) + coef * u(2,1) = 0``, i.e. ``u(1,1) = -coef * u(2,1)``: the same
    slave dof, the same master dof, one record each on the pair (1, 2).
    """
    n1, n2 = ada.Node((0, 0, 0), 1), ada.Node((1, 0, 0), 2)
    fem = ada.FEM("MyFem", nodes=ada.api.containers.Nodes([n1, n2]))
    s_set = fem.add_set(FemSet("dep", [n1], "nset"))
    m_set = fem.add_set(FemSet("indep", [n2], "nset"))
    for i, coef in enumerate(coefs, start=1):
        fem.add_constraint(
            Constraint(
                f"eq{i}",
                Constraint.TYPES.EQUATION,
                m_set,
                s_set,
                equation_terms=[(n1, 1, 1.0), (n2, 1, coef)],
                parent=fem,
            )
        )
    return fem


def test_the_same_dependency_declared_twice_is_written_once():
    fem = _two_node_fem(-1.0, -1.0)

    with conversion_report.collect() as report:
        records = bldep_records(fem)

    assert len(records) == 1
    # Not ((1, 1, 1.0), (1, 1, 1.0)) -- Sestra would add the two betas and make it u1 = 2 * u2.
    assert records[0].terms == ((1, 1, 1.0),)
    assert records[0].slave_dofs == (1,)

    notes = [f for f in report.of_kind(conversion_report.NOTE) if f.subject == "eq2"]
    assert len(notes) == 1
    assert notes[0].details["other"] == "eq1"
    assert notes[0].details["beta"] == 1.0


def test_two_different_betas_on_one_dof_pair_refuse_the_later_one():
    fem = _two_node_fem(-1.0, -0.5)

    with conversion_report.collect() as report:
        records = bldep_records(fem)

    assert len(records) == 1
    # The first-declared relation, unchanged: not 1.5 (the sum), not 0.5 (the last one).
    assert records[0].terms == ((1, 1, 1.0),)

    omitted = [f for f in report.of_kind(conversion_report.OMITTED) if f.subject == "eq2"]
    assert len(omitted) == 1
    assert omitted[0].details == {
        "node": 1,
        "dof": 1,
        "master": 2,
        "beta": 0.5,
        "kept": 1.0,
        "other": "eq1",
    }


def test_terms_of_different_dof_pairs_still_share_one_record():
    """The merge itself is untouched: two equations on one node pair but different dofs are
    still one record, as the manual requires, with both terms on it."""
    fem = _two_node_fem(-1.0)
    n1, n2 = fem.nodes.from_id(1), fem.nodes.from_id(2)
    fem.add_constraint(
        Constraint(
            "eq_z",
            Constraint.TYPES.EQUATION,
            fem.nsets["indep"],
            fem.nsets["dep"],
            equation_terms=[(n1, 3, 1.0), (n2, 3, -2.0)],
            parent=fem,
        )
    )

    with conversion_report.collect():
        records = bldep_records(fem)

    assert len(records) == 1
    assert records[0].terms == ((1, 1, 1.0), (3, 3, 2.0))
    assert records[0].slave_dofs == (1, 3)
