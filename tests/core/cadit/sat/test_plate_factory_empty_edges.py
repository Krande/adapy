"""Regression: empty edge list after whisker drop must not crash.

Some SAT loops in real Genie XML exports consist entirely of paired
"whisker" coedges. ``_drop_whisker_coedges``
removes them, leaving an empty edge list. The previous code indexed
``edges[0]`` on the way into ``get_points`` and exploded with a bare
IndexError, aborting the entire conversion. The fix routes that case
through the same ``ACISInsufficientPointsError`` the surrounding code
already handles with a per-face skip-and-warn.
"""

from __future__ import annotations

import pytest

from ada.cadit.sat.exceptions import ACISInsufficientPointsError
from ada.cadit.sat.read.faces import PlateFactory


def test_get_points_on_empty_edges_raises_insufficient_points():
    factory = PlateFactory.__new__(PlateFactory)  # bypass __init__; we don't touch sat_store
    with pytest.raises(ACISInsufficientPointsError):
        factory.get_points([])
