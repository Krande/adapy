"""Regression: the Calculix FRD reader must not drop the last element.

``collect_elements`` appends each element when the *next* element's
``-1`` header line is seen, then exits the loop on the block terminator
(``-3``). The final accumulated element therefore has no following
header to trigger its append — without an explicit post-loop flush it
was silently dropped, losing exactly one element per mesh (observed as a
missing corner element in the Calculix shell→solid expansion render).
"""

from ada.fem.formats.calculix.results.read_frd_file import CcxResultModel


def _frd_element_block(n_elements: int, nodes_per_elem: int = 8) -> str:
    """Minimal FRD ``3C`` element block with ``n_elements`` HEX8-style
    records: a ``-1 <id> <type> <grp> <mat>`` header + one ``-2`` node
    line each, terminated by ``-3``."""

    lines = ["3C"]
    for eid in range(1, n_elements + 1):
        lines.append(f" -1{eid:10d}    1    0    1")
        conn = "".join(f"{eid * 100 + k:10d}" for k in range(nodes_per_elem))
        lines.append(f" -2{conn}")
    lines.append(" -3")
    return "\n".join(lines) + "\n"


def test_collect_elements_keeps_last_element():
    n = 5
    model = CcxResultModel(iter(_frd_element_block(n).splitlines()))
    model.load()

    # All n elements parsed (previously n-1 — the last was dropped).
    assert model.elements.shape[0] == n
    # 4 metadata cols (id, type, group, material) + 8 node refs.
    assert model.elements.shape[1] == 4 + 8
    # The last element's id survived rather than being truncated away.
    assert int(model.elements[-1, 0]) == n
