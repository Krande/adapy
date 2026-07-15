from ada.fem.capacity.girder import _adjacent_stiffener_spans


def test_adjacent_stiffener_spans_from_two_sides() -> None:
    assert _adjacent_stiffener_spans({-1: 1.3, 1: 1.625}, 2.925) == (2.6, 3.25)


def test_adjacent_stiffener_spans_mirrors_single_side() -> None:
    assert _adjacent_stiffener_spans({1: 1.4}, 2.8) == (2.8, 2.8)
