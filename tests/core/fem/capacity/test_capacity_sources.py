from __future__ import annotations

import numpy as np


def test_concept_named_stiffener_runs_join_connected_colinear_segments(monkeypatch):
    from ada.fem.capacity import extract, sources

    class Index:
        elem_nodes = {
            1: (1, 2),
            2: (2, 3),
            3: (10, 11),
            4: (3, 4),
        }

    coords = {
        1: np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]]),
        2: np.array([[1.0, 0.0, 0.0], [2.0, 0.0, 0.0]]),
        3: np.array([[0.0, 1.0, 0.0], [1.0, 1.0, 0.0]]),
        4: np.array([[2.0, 0.0, 0.0], [2.0, 1.0, 0.0]]),
    }

    monkeypatch.setattr(extract, "_ensure_index", lambda _mesh: Index())
    monkeypatch.setattr(extract, "element_node_coords", lambda _mesh, element_id: coords[element_id])
    monkeypatch.setattr(extract, "geono_of", lambda _mesh, _element_id: 7)

    runs = sources._stiffener_runs(None, [1, 2, 3, 4], {1: "S1", 2: "S1", 3: "S1", 4: "S1"})

    assert runs == [(1, 2), (3,), (4,)]


def test_unnamed_stiffener_runs_keep_one_element_fallback():
    from ada.fem.capacity import sources

    assert sources._stiffener_runs(None, [3, 1, 2], {}) == [(3,), (1,), (2,)]


def test_named_run_splits_at_transverse_girder(monkeypatch):
    """A concept name spans the whole stiffener; the run is cut at supports.

    Four colinear beams (1-4) form one named stiffener; a perpendicular girder
    (10, not a secondary stiffener) crosses at the interior node between beams 2
    and 3. The DNV span is the bay between supports, so the run splits there.
    """
    from ada.fem.capacity import extract, sources

    class Index:
        elem_nodes = {1: (1, 2), 2: (2, 3), 3: (3, 4), 4: (4, 5), 10: (3, 20)}
        node_elems = {1: [1], 2: [1, 2], 3: [2, 3, 10], 4: [3, 4], 5: [4], 20: [10]}

    coords = {
        1: np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]]),
        2: np.array([[1.0, 0.0, 0.0], [2.0, 0.0, 0.0]]),
        3: np.array([[2.0, 0.0, 0.0], [3.0, 0.0, 0.0]]),
        4: np.array([[3.0, 0.0, 0.0], [4.0, 0.0, 0.0]]),
        10: np.array([[2.0, 0.0, 0.0], [2.0, 1.0, 0.0]]),  # perpendicular girder
    }

    monkeypatch.setattr(extract, "_ensure_index", lambda _mesh: Index())
    monkeypatch.setattr(extract, "element_node_coords", lambda _mesh, element_id: coords[element_id])

    runs = sources._split_runs_at_supports(
        None,
        [(1, 2, 3, 4)],
        secondary_ids={1, 2, 3, 4},
        all_line_ids={1, 2, 3, 4, 10},
        all_shell_ids=set(),
    )

    assert runs == [(1, 2), (3, 4)]


def test_named_run_without_supports_stays_whole(monkeypatch):
    from ada.fem.capacity import extract, sources

    class Index:
        elem_nodes = {1: (1, 2), 2: (2, 3), 3: (3, 4)}
        node_elems = {1: [1], 2: [1, 2], 3: [2, 3], 4: [3]}

    coords = {
        1: np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]]),
        2: np.array([[1.0, 0.0, 0.0], [2.0, 0.0, 0.0]]),
        3: np.array([[2.0, 0.0, 0.0], [3.0, 0.0, 0.0]]),
    }

    monkeypatch.setattr(extract, "_ensure_index", lambda _mesh: Index())
    monkeypatch.setattr(extract, "element_node_coords", lambda _mesh, element_id: coords[element_id])

    runs = sources._split_runs_at_supports(
        None, [(1, 2, 3)], secondary_ids={1, 2, 3}, all_line_ids={1, 2, 3}, all_shell_ids=set()
    )

    assert runs == [(1, 2, 3)]


def test_flatbar_name_fallback_supplies_capacity_dimensions():
    from ada.fem.capacity.stiffened_plate import _flatbar_from_name

    assert _flatbar_from_name("Fbar150x10") == (0.15, 0.01)
    assert _flatbar_from_name("FB325_5x12_5") == (0.3255, 0.0125)
    assert _flatbar_from_name("HP180x8") is None
