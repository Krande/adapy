from __future__ import annotations


class ACISInsufficientPointsError(Exception):
    pass


class ACISReferenceDataError(Exception):
    pass


class ACISUnsupportedSurfaceType(Exception):
    pass


class ACISIncompleteCtrlPoints(Exception):
    pass


class ACISUnsupportedCurveType(Exception):
    pass


class ACISDegenerateEdge(Exception):
    """An edge with no curve — ACIS marking a singularity, not a boundary.

    Its two vertices are the same point and its box is that point, so it
    contributes nothing to the face's boundary: where a spline patch collapses
    to a point, the loop runs into the singularity and back out, and this is
    the step between. A hull export carries 48, on 38 of its 5470 faces.
    """
