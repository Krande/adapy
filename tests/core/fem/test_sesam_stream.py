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
    (q_ids, q_conn) = next(iter(by_type.values()))
    assert q_conn[0] == [1, 2, 3, 4]
    # the mass record captured its (wrapped) node id, not leaked
    assert 2 in mass_elem
    assert int(float(mass_elem[2]["gelmnt"]["nids"].split()[0])) == 10
    # the text bucket holds exactly the one non-mesh card (BNMASS) — no leaked GELMNT1
    # continuation line of bare node ids alongside it
    text_lines = [ln for ln in other.splitlines() if ln.strip()]
    assert len(text_lines) == 1
    assert text_lines[0].lstrip().startswith("BNMASS")
