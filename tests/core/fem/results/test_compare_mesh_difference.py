"""mesh_difference's element comparison, as arrays, against the element-by-element walk it replaced."""

from __future__ import annotations

import copy

import numpy as np
import pytest

from ada.fem.results.compare import mesh_difference


def _walk_connectivity(mesh) -> dict[int, tuple]:
    out = {}
    for block in mesh.elements or []:
        refs = block.node_refs
        if block.node_refs_are_indices:
            ids = np.asarray(mesh.nodes.identifiers)
            refs = [ids[np.asarray(r, dtype=int)] for r in refs]
        for label, r in zip(block.identifiers, refs):
            out[int(label)] = tuple(int(x) for x in np.asarray(r).ravel())
    return out


def _walk_difference(a, b) -> str | None:
    """The element part of mesh_difference as it was: dicts, then a walk in a's order."""
    a_conn, b_conn = _walk_connectivity(a), _walk_connectivity(b)
    if len(a_conn) != len(b_conn):
        return f"{len(a_conn)} vs {len(b_conn)} elements"
    if a_conn.keys() != b_conn.keys():
        return "different element ids"
    for el_id, refs in a_conn.items():
        if refs != b_conn[el_id]:
            return f"element {el_id} has different nodes"
    return None


@pytest.fixture(scope="module")
def mesh():
    from ada.fem.formats.sesam.results.read_sin import read_sin_file

    from ..formats.sesam.sin_assembly import SIN_PATH

    return read_sin_file(SIN_PATH).mesh


def _edited(mesh, edit):
    other = copy.deepcopy(mesh)
    edit(other.elements[0])
    return other


def _swap_nodes(rows):
    def edit(block):
        refs = np.array(block.node_refs)
        for r in rows:
            refs[r, [0, 1]] = refs[r, [1, 0]]
        block.node_refs = refs

    return edit


def _relabel(block):
    ids = np.array(block.identifiers)
    ids[5] = ids.max() + 100
    block.identifiers = ids


def _duplicate_last(block):
    # A repeated id: the later definition is the one that counts.
    block.identifiers = np.concatenate([block.identifiers, block.identifiers[:1]])
    block.node_refs = np.concatenate([block.node_refs, block.node_refs[:1][:, ::-1]])


def _drop_one(block):
    block.identifiers = block.identifiers[:-1]
    block.node_refs = block.node_refs[:-1]


def _as_ragged(block):
    block.node_refs = [np.asarray(r) for r in block.node_refs]


@pytest.mark.parametrize(
    "edit",
    [
        lambda b: None,
        _swap_nodes([40]),
        _swap_nodes([200, 7, 120]),  # reported: the first in element order, not the first by id
        _relabel,
        _duplicate_last,
        _drop_one,
        _as_ragged,
    ],
)
def test_matches_the_walk(mesh, edit):
    other = _edited(mesh, edit)
    assert mesh_difference(mesh, other) == _walk_difference(mesh, other)
    assert mesh_difference(other, mesh) == _walk_difference(other, mesh)


def test_shuffled_element_order_is_the_same_mesh(mesh):
    def shuffle(block):
        order = np.random.default_rng(1).permutation(len(block.identifiers))
        block.identifiers = np.asarray(block.identifiers)[order]
        block.node_refs = np.asarray(block.node_refs)[order]

    other = _edited(mesh, shuffle)
    assert mesh_difference(mesh, other) is None
    swapped = _edited(other, _swap_nodes([3, 300]))
    assert mesh_difference(mesh, swapped) == _walk_difference(mesh, swapped)
