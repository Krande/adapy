"""``*System`` moves the mesh, so skipping it is not a missing construct but a wrong coordinate.

The keyword census already said ``*SYSTEM`` was unread (``test_unread_keywords``), which is the
honest half of the old behaviour: it could say the keyword went nowhere, but not that the nodes
after it were in the wrong place. These tests pin where the nodes land, and pin the one case the
reader refuses to guess at.
"""

from __future__ import annotations

import numpy as np
import pytest

import ada
from ada.fem.formats import conversion_report
from ada.fem.formats.abaqus.read.read_systems import find_local_systems, system_in_force

_TAIL = "\n".join(
    [
        "*Element, type=C3D4, elset=solid",
        "1, 1, 2, 3, 4",
        "*Solid Section, elset=solid, material=steel",
        ",",
        "*Material, name=steel",
        "*Elastic",
        "2.1e11, 0.3",
        "*Density",
        "7850.,",
        "",
    ]
)

_NODES = "\n".join(["*Node", "1, 0., 0., 0.", "2, 1., 0., 0.", "3, 0., 1., 0.", "4, 0., 0., 1.", ""])


def _read(tmp_path, head: str = ""):
    path = tmp_path / "deck.inp"
    path.write_text(head + _NODES + _TAIL)
    with conversion_report.collect() as report:
        a = ada.from_fem(path, "abaqus")
    return a, report


def _points(a: ada.Assembly) -> dict[int, tuple[float, float, float]]:
    return {
        n.id: tuple(round(float(x), 9) for x in n.p)
        for part in a.get_all_parts_in_assembly(True)
        for n in part.fem.nodes
    }


def test_no_system_leaves_the_coordinates_exactly_as_written(tmp_path):
    a, report = _read(tmp_path)
    assert _points(a)[1] == (0.0, 0.0, 0.0) and _points(a)[2] == (1.0, 0.0, 0.0)
    assert not [f for f in report.findings if f.keyword == "*SYSTEM"]


def test_a_three_number_system_translates_the_nodes(tmp_path):
    """The case that was silently wrong: node 1 read at the origin instead of at ``a``."""
    a, report = _read(tmp_path, "*System\n0., -660., 0.\n")
    assert _points(a)[1] == (0.0, -660.0, 0.0), "the local origin must be added to every coordinate"
    assert _points(a)[2] == (1.0, -660.0, 0.0)
    (note,) = [f for f in report.findings if f.keyword == "*SYSTEM"]
    assert note.kind == "note" and note.details["nodes"] == 4


def test_a_six_number_system_along_global_x_is_a_translation(tmp_path):
    """``a`` to ``b`` along global X: the rotation is the identity, so the translation applies."""
    a, _ = _read(tmp_path, "*System\n0., -660., 0., 1., -660., 0.\n")
    assert _points(a)[1] == (0.0, -660.0, 0.0)


def test_a_six_number_system_off_axis_is_reported_and_not_applied(tmp_path):
    """Without a point in the X-Y plane the local Y and Z axes are not determined. A guessed
    completion rule would move real nodes by it, so nothing is applied and the gap is named."""
    a, report = _read(tmp_path, "*System\n0., 0., 0., 0., 1., 0.\n")
    assert _points(a)[2] == (1.0, 0.0, 0.0), "coordinates must be untouched when the frame is undetermined"
    (finding,) = [f for f in report.findings if f.keyword == "*SYSTEM"]
    assert finding.kind == "omitted" and finding.details["nodes"] == 4
    assert "X-Y plane" in finding.reason


def test_a_nine_number_system_rotates_and_translates(tmp_path):
    """Local X along global Y, local Y along global -X: a 90-degree turn about Z, plus an offset."""
    a, _ = _read(tmp_path, "*System\n10., 0., 0., 10., 1., 0., 9., 0., 0.\n")
    p = _points(a)
    # local (1,0,0) -> global +Y, local (0,1,0) -> global -X, both from the origin at (10, 0, 0)
    assert p[2] == (10.0, 1.0, 0.0)
    assert p[3] == (9.0, 0.0, 0.0)
    assert p[4] == (10.0, 0.0, 1.0), "local Z is unchanged by a turn about it"


def test_a_system_that_spells_out_the_global_frame_changes_nothing(tmp_path):
    """Nine numbers that describe the global axes must read back byte-for-byte, not through a
    matrix multiply that rounds."""
    a, report = _read(tmp_path, "*System\n0., 0., 0., 1., 0., 0., 0., 1., 0.\n")
    assert _points(a) == {1: (0.0, 0.0, 0.0), 2: (1.0, 0.0, 0.0), 3: (0.0, 1.0, 0.0), 4: (0.0, 0.0, 1.0)}
    assert not [f for f in report.findings if f.keyword == "*SYSTEM"]


def test_an_empty_system_resets_to_global(tmp_path):
    """A bare ``*System`` closes the one before it -- which is what makes reading the keyword in
    document order enough. (The user's own 80 MB deck carries exactly one, and it is this.)"""
    head = "*System\n0., -660., 0.\n" + _NODES + "*System\n"
    path = tmp_path / "deck.inp"
    path.write_text(head + "*Node\n5, 2., 0., 0.\n" + _TAIL)
    with conversion_report.collect():
        a = ada.from_fem(path, "abaqus")
    p = _points(a)
    assert p[1] == (0.0, -660.0, 0.0), "the first block is under the local system"
    assert p[5] == (2.0, 0.0, 0.0), "the block after the reset is global"


def test_the_system_in_force_is_the_last_one_before_the_block():
    text = "*System\n1., 0., 0.\n*Node\n1, 0., 0., 0.\n*System\n2., 0., 0.\n*Node\n2, 0., 0., 0.\n"
    systems = find_local_systems(text)
    assert [float(s.origin[0]) for s in systems] == [1.0, 2.0]
    first_node = text.index("*Node")
    second_node = text.index("*Node", first_node + 1)
    assert system_in_force(systems, first_node).origin[0] == 1.0
    assert system_in_force(systems, second_node).origin[0] == 2.0
    assert system_in_force(systems, 0) is None


@pytest.mark.parametrize(
    "data, reason",
    [
        ("1., 2.\n", "carries 2 numbers"),
        ("0., 0., 0., 0., 0., 0.\n", "on top of the origin"),
        ("0., 0., 0., 1., 0., 0., 2., 0., 0.\n", "on the local X axis"),
    ],
)
def test_a_malformed_system_raises_rather_than_guessing(data, reason):
    """No correct subset of the input can be written: every node after such a block would be at a
    coordinate nobody can work out. That is the one case the report rule leaves fatal."""
    with pytest.raises(ValueError, match=reason):
        find_local_systems("*System\n" + data)


def test_both_node_readers_agree(tmp_path):
    """``get_nodes_from_inp_arrays`` is the packed path the array-backed mesh takes. Two node
    readers that disagree about where a node is would be worse than neither applying the
    transform."""
    from ada.fem.formats.abaqus.read.reader import get_nodes_from_inp_arrays

    text = "*System\n0., -660., 0.\n" + _NODES
    coords, ids, _ = get_nodes_from_inp_arrays(text)
    assert list(ids) == [1, 2, 3, 4]
    assert np.allclose(coords[0], [0.0, -660.0, 0.0])
    assert np.allclose(coords[1], [1.0, -660.0, 0.0])
