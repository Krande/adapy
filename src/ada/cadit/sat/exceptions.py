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


class ACISBinaryBodyError(Exception):
    """An ACIS body saved in binary (SAB) where this reader needs text (SAT).

    GeniE V9.3 writes a workspace's body as ``acisGeometry.sab`` when its
    compatibility option "Write ACIS files in binary format"
    (``GenieRules.Compatibility.enable(WriteACISBinaryFile, true)``) is on, and
    GeniE itself picks the format by that member name alone. The text reader
    would find no records in it: measured on V9.3 workspaces, every beam read
    and every plate silently vanished. Hence a refusal by name, raised before
    the body reaches the SAT parser.
    """


class ACISDegenerateEdge(Exception):
    """An edge with no curve — ACIS marking a singularity, not a boundary.

    Its two vertices are the same point and its box is that point, so it
    contributes nothing to the face's boundary: where a spline patch collapses
    to a point, the loop runs into the singularity and back out, and this is
    the step between. A hull export carries 48, on 38 of its 5470 faces.
    """
