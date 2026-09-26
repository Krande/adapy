"""A ``*Coupling`` followed by ``*Distributing`` is a coupling, and used to be nothing at all.

The reader matched only ``*Kinematic``. A ``*Coupling`` followed by ``*Distributing`` -- which is
what Abaqus/CAE writes for a distributing coupling, in three spellings depending on the element
types -- matched nothing, so the whole constraint left the model with a ``logger.warning`` and no
entry in the conversion report. A log line is not where an omitted construct belongs: the report
is what ``ada convert`` writes out and what ``--strict`` fails on.

Reading it is only half the job. A distributing coupling spreads the load over its surface by
weights; the Sesam writer has only rigid links, which is the *kinematic* form. So the type the
reader saw is kept on the constraint, the Abaqus writer puts the same sub-keyword back, and the
Sesam writer says which approximation it made -- instead of silently writing one as the other.
"""

from __future__ import annotations

import pytest

import ada
from ada.fem.formats import conversion_report
from ada.fem.formats.abaqus.write.write_constraints import constraint_str
from ada.fem.formats.sesam.write.write_constraints import bldep_records

_PART = "\n".join(
    [
        "*Part, name=p",
        "*Node",
        "1, 0., 0., 0.",
        "2, 1., 0., 0.",
        "3, 1., 1., 0.",
        "4, 0., 1., 0.",
        "*Element, type=S4, elset=plate",
        "1, 1, 2, 3, 4",
        "*Nset, nset=top",
        "1, 2, 3, 4",
        "*Shell Section, elset=plate, material=steel",
        "0.01, 5",
        "*End Part",
    ]
)

_TAIL = "\n".join(["*Material, name=steel", "*Elastic", "2.1e11, 0.3", "*Density", "7850.,", ""])


def _deck(sub: str, dofs: str = "1, 6\n") -> str:
    return (
        "\n".join(
            [
                _PART,
                "*Assembly, name=a",
                "*Instance, name=p-1, part=p",
                "*End Instance",
                "*Node",
                "99, 0.5, 0.5, 1.",
                "*Nset, nset=rp",
                "99,",
                "*Surface, type=NODE, name=top_surf",
                "p-1.top, 1.",
                "*Coupling, constraint name=c1, ref node=rp, surface=top_surf",
                sub,
                "",
            ]
        )
        + dofs
        + "*End Assembly\n"
        + _TAIL
    )


def _read(tmp_path, text: str):
    path = tmp_path / "deck.inp"
    path.write_text(text)
    with conversion_report.collect() as report:
        a = ada.from_fem(path, "abaqus")
    return a, report


def _coupling(a: ada.Assembly):
    return a.fem.constraints["c1"]


@pytest.mark.parametrize("sub", ["*Distributing", "*Structural Distributing", "*Continuum Distributing"])
def test_every_spelling_of_the_distributing_sub_keyword_is_read(tmp_path, sub):
    """All three spellings are one coupling type. Before this each of them lost the constraint."""
    a, report = _read(tmp_path, _deck(sub))
    c = _coupling(a)
    assert c.type == "coupling"
    assert c.metadata["coupling_type"] == "distributing"
    assert not [f for f in report.findings if f.keyword == "*COUPLING"], "nothing was omitted"


def test_a_kinematic_coupling_still_reads_as_kinematic(tmp_path):
    a, _ = _read(tmp_path, _deck("*Kinematic"))
    assert _coupling(a).metadata["coupling_type"] == "kinematic"


def test_a_coupling_with_no_sub_keyword_is_reported_not_logged(tmp_path):
    """Nothing says what it couples, so the constraint cannot be built -- but it must appear in
    the report, which is what a caller reads to know the conversion was lossy."""
    text = _deck("*Nset, nset=spare", "99,\n")
    a, report = _read(tmp_path, text)
    assert "c1" not in a.fem.constraints
    (finding,) = [f for f in report.findings if f.keyword == "*COUPLING"]
    assert finding.kind == "omitted" and finding.subject == "c1"
    assert finding.details["followed_by"] == "*NSET"


def test_the_abaqus_writer_puts_the_same_sub_keyword_back(tmp_path):
    """Writing a distributing coupling out as ``*Kinematic`` would be a different constraint --
    rigid instead of load-spreading."""
    a, _ = _read(tmp_path, _deck("*Distributing"))
    out = constraint_str(_coupling(a), True).upper()
    assert "*DISTRIBUTING" in out and "*KINEMATIC" not in out

    b, _ = _read(tmp_path, _deck("*Kinematic"))
    assert "*KINEMATIC" in constraint_str(_coupling(b), True).upper()


def test_the_sesam_writer_says_a_distributing_coupling_became_rigid_links(tmp_path):
    """BLDEP has no weighted form, so the rigid one is written -- and named as an approximation."""
    a, _ = _read(tmp_path, _deck("*Distributing"))
    with conversion_report.collect() as report:
        records = bldep_records(a.fem)
    assert records, "the coupling is still written"
    reasons = [f.reason for f in report.findings if f.kind == "approximated" and f.subject == "c1"]
    assert any("distributing coupling is written as rigid links" in r for r in reasons), reasons

    b, _ = _read(tmp_path, _deck("*Kinematic"))
    with conversion_report.collect() as report:
        bldep_records(b.fem)
    assert not [f for f in report.findings if "distributing" in f.reason], "only a distributing one says this"


def test_a_sub_keyword_data_line_with_one_dof_is_read(tmp_path):
    """``*Kinematic`` followed by a bare ``1,`` constrains DOF 1. The old reshape(-1, 2) raised
    ``ValueError`` on the odd count and took the whole import down."""
    a, _ = _read(tmp_path, _deck("*Kinematic", "1,\n"))
    from ada.fem.constraints import expand_dofs

    assert expand_dofs(_coupling(a).dofs) == (1,)


def test_an_empty_sub_keyword_couples_all_six_dofs(tmp_path):
    """Abaqus couples every DOF the nodes have when the block lists none, and says so."""
    a, report = _read(tmp_path, _deck("*Kinematic", ""))
    from ada.fem.constraints import expand_dofs

    assert expand_dofs(_coupling(a).dofs) == (1, 2, 3, 4, 5, 6)
    assert [f.kind for f in report.findings if f.keyword == "*KINEMATIC"] == ["note"]
