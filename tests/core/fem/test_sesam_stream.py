"""Streaming Sesam .FEM reader edge cases (stream_fem_mesh)."""

from ada.fem.formats.sesam.read.stream import stream_fem_mesh


def _f(*vals) -> str:
    return "    " + "    ".join(f"{v:.8E}" for v in vals) + "\n"


def test_stream_wrapping_mass_gelmnt1_does_not_leak(tmp_path):
    """A mass/spring GELMNT1 whose node ids wrap onto a continuation line must not leak
    those ids into ``other_text`` (regression: the continuation of a non-structural
    GELMNT1 was bucketed as text, corrupting the following BNMASS card)."""
    fem = tmp_path / "m.FEM"
    lines = []
    # GCOORD: GCOORD fieldno nodeno x y z
    for nid in (1, 2, 3, 4, 10):
        lines.append("GCOORD" + _f(nid, nid, float(nid), 0.0, 0.0))
    # structural QUAD (eltyp 24), node ids on a continuation line
    lines.append("GELMNT1" + _f(1, 1, 24, 0))
    lines.append(_f(1, 2, 3, 4))
    # MASS (eltyp 11) whose single node id wraps onto a continuation line — the bug
    lines.append("GELMNT1" + _f(2, 2, 11, 0))
    lines.append(_f(10))
    # a following non-mesh card that previously got corrupted by the leaked node id
    lines.append("BNMASS" + _f(10, 6, 1.5, 1.5, 1.5, 0.0, 0.0, 0.0))
    fem.write_text("".join(lines))

    coords, node_ids, by_type, mass_elem, spring_elem, ext_map, other = stream_fem_mesh(str(fem))

    # the structural quad is parsed with its 4 nodes
    assert sum(len(ids) for ids, _ in by_type.values()) == 1
    q_ids, q_conn = next(iter(by_type.values()))
    assert q_conn[0] == [1, 2, 3, 4]
    # the mass record captured its (wrapped) node id, not leaked
    assert 2 in mass_elem
    assert int(float(mass_elem[2]["gelmnt"]["nids"].split()[0])) == 10
    # the text bucket holds exactly the one non-mesh card (BNMASS) — no leaked GELMNT1
    # continuation line of bare node ids alongside it
    text_lines = [ln for ln in other.splitlines() if ln.strip()]
    assert len(text_lines) == 1
    assert text_lines[0].lstrip().startswith("BNMASS")


def test_stream_counts_unread_cards_instead_of_keeping_them(tmp_path):
    """BELOAD1 (no reader has a card for it) is counted, continuation lines included, and kept
    out of ``other_text``: on a loaded SESTRA deck it is nine tenths of the file, and every
    object reader would regex over it."""
    fem = tmp_path / "l.FEM"
    lines = ["GCOORD" + _f(1, 1, 0.0, 0.0, 0.0)]
    for elno in (1, 2):
        lines.append("BELOAD1" + _f(1, 1, 0, 0))
        lines.append(_f(elno, 0.0, 0.0, 6))
        lines.append(_f(0, 1.0, 2.0, 3.0))
    lines.append("BNMASS" + _f(1, 6, 1.5, 1.5, 1.5, 0.0, 0.0, 0.0))
    fem.write_text("".join(lines))

    unread: dict[str, int] = {}
    *_, other = stream_fem_mesh(str(fem), unread)

    assert unread == {"BELOAD1": 2}
    text_lines = [ln for ln in other.splitlines() if ln.strip()]
    assert len(text_lines) == 1
    assert text_lines[0].lstrip().startswith("BNMASS")


def test_a_load_case_name_given_to_several_llcs_stays_one_case_each():
    """SESTRA repeats its case names across LLCs; keyed by name, the later case replaced the
    earlier and its loads were left in no case, so the writer dropped them."""
    from ada.fem.formats.sesam.read.read_loads import load_case_names
    from ada.fem.formats.sesam.write.write_loads import _case_str

    deck = _case_str(1, "LC1") + _case_str(2, "WAVE") + _case_str(93, "LC1") + _case_str(140, "LC1")
    assert load_case_names(deck) == {1: "LC1", 2: "WAVE", 93: "LC1_93", 140: "LC1_140"}


def test_unread_load_counts_are_reported():
    from ada.fem.formats import conversion_report
    from ada.fem.formats.sesam.read.read_loads import report_unread_load_records

    with conversion_report.collect() as rep:
        counts = report_unread_load_records("", {"BELOAD1": 7, "GCOORD": 3})
    assert counts == {"BELOAD1": 7}
    assert [(f.keyword, f.count) for f in rep.findings] == [("BELOAD1", 7)]
