"""The face normal, read off a SAT file rather than assumed.

ACIS splits it across two records: the ``face`` carries a forward/reversed
sense and a ``spline-surface`` carries one of its own, so the normal is the
composition. The reader used to hardcode ``same_sense = True``, which is a
claim rather than a reading — a Genie export writes ``reversed`` on 1248 of
4532 spline surfaces and on 112 plane faces, and every one came back flipped.
"""

import pathlib

import pytest

from ada.cadit.sat.read.advanced_face import get_face_same_sense
from ada.cadit.sat.store import SatReaderFactory

HEADER = "2000 0 1 0\n18 SESAM - gmGeometry 14 ACIS 33.0.1 NT 24 Tue Jan 17 20:39:08 2023\n1000 1e-06 1e-10\n"

# a face, its loop, one plane-surface, and the minimum below it to resolve
_PLANE_MODEL = """-0 body $-1 -1 -1 $-1 $1 $-1 $-1 T 0 0 0 1 1 0 #
-1 lump $-1 -1 -1 $-1 $-1 $2 $0 T 0 0 0 1 1 0 #
-2 shell $-1 -1 -1 $-1 $-1 $-1 $3 $-1 $1 T 0 0 0 1 1 0 #
-3 face $-1 -1 -1 $-1 $-1 $4 $2 $-1 $5 {face_sense} double out F F #
-4 loop $-1 -1 -1 $-1 $-1 $-1 $3 T 0 0 0 1 1 0 unknown #
-5 plane-surface $-1 -1 -1 $-1 0 0 0 0 0 1 1 0 0 forward_v I I I I #
End-of-ACIS-data"""

_SPLINE_MODEL = """-0 body $-1 -1 -1 $-1 $1 $-1 $-1 T 0 0 0 1 1 0 #
-1 lump $-1 -1 -1 $-1 $-1 $2 $0 T 0 0 0 1 1 0 #
-2 shell $-1 -1 -1 $-1 $-1 $-1 $3 $-1 $1 T 0 0 0 1 1 0 #
-3 face $-1 -1 -1 $-1 $-1 $4 $2 $-1 $5 {face_sense} double out F F #
-4 loop $-1 -1 -1 $-1 $-1 $-1 $3 T 0 0 0 1 1 0 unknown #
-5 spline-surface $-1 -1 -1 $-1 {surf_sense} {{ exactsur full nurbs 1 1 both open open none none 2 2 \n\t0 1 1 1 \n\t0 1 1 1 \n\t0 0 0 1 \n\t1 0 0 1 \n\t0 1 0 1 \n\t1 1 0 1 \n\t0 \n\t0 \n\t0 \n\t0 \n\t0 \n\t0 \n\t0 \n\tF 1 F 0 F 1 F 0 }} I I I I #
End-of-ACIS-data"""


def _face_record(tmp_path: pathlib.Path, text: str, name: str):
    path = tmp_path / f"{name}.sat"
    path.write_text(HEADER + text)
    rf = SatReaderFactory(path)
    return next(iter(rf.iter_faces()))


class TestPlaneFace:
    """A plane-surface has no sense of its own; the face carries it."""

    @pytest.mark.parametrize("face_sense,expected", [("forward", True), ("reversed", False)])
    def test_the_face_sense_is_the_answer(self, tmp_path, face_sense, expected):
        rec = _face_record(tmp_path, _PLANE_MODEL.format(face_sense=face_sense), face_sense)
        assert get_face_same_sense(rec) is expected


class TestSplineFace:
    """Both records carry a sense; the normal is the composition."""

    @pytest.mark.parametrize(
        "face_sense,surf_sense,expected",
        [
            ("forward", "forward", True),
            ("forward", "reversed", False),
            ("reversed", "forward", False),
            # two flips cancel
            ("reversed", "reversed", True),
        ],
    )
    def test_the_senses_compose(self, tmp_path, face_sense, surf_sense, expected):
        text = _SPLINE_MODEL.format(face_sense=face_sense, surf_sense=surf_sense)
        rec = _face_record(tmp_path, text, f"{face_sense}_{surf_sense}")
        assert get_face_same_sense(rec) is expected
