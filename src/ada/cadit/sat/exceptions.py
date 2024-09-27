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
