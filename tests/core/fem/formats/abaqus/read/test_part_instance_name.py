"""A flat deck is named after its first ``** PART INSTANCE:`` comment.

Abaqus/CAE writes a flat deck (no ``*Part``/``*Assembly``) with a ``** PART INSTANCE:`` comment
above each instance's own ``*Node`` block. The regex reader named the part after the first; the
lexer port required exactly one such block and otherwise fell back to a generated name -- the
same model under a different name in every consumer.
"""

from __future__ import annotations

import pytest

import ada


def _instance(i: int) -> str:
    """One instance's comment, nodes and element, with ids that do not collide."""
    n = 10 * i
    return (
        f"** PART INSTANCE: inst-{i}\n"
        f"*Node\n{n + 1}, 0., 0., {i}.\n{n + 2}, 1., 0., {i}.\n{n + 3}, 1., 1., {i}.\n{n + 4}, 0., 0., {i + 0.5}\n"
        f"*Element, type=C3D4, elset=solid{i}\n{n + 1}, {n + 1}, {n + 2}, {n + 3}, {n + 4}\n"
    )


@pytest.mark.parametrize("n_instances", [1, 2, 3])
def test_a_flat_deck_is_named_after_its_first_part_instance_comment(tmp_path, n_instances):
    path = tmp_path / "deck.inp"
    path.write_text("".join(_instance(i) for i in range(1, n_instances + 1)))
    a = ada.from_fem(path, "abaqus")

    parts = [p for p in a.get_all_parts_in_assembly() if not p.fem.is_empty()]
    assert [p.name for p in parts] == ["inst-1"]
    # and every instance's mesh is still read, into that one part
    assert len(parts[0].fem.elements) == n_instances
