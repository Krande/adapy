"""GeniE's rotated supports through the Sesam reader and writer (``genie_supports_frames_T1.FEM``, GeniE V8.13-02).

At a node GeniE transformed (BNDOF / BNTRCOS) the BNBCD codes are in the node's frame. They used to be read as if
global. Now they come in as a ``Bc`` with that frame as its ``csys``, and go back out either as the global support
the frame maps onto or as a transformed node of their own. Either way, the directions a node is held in must be the
same: per node, the projector onto the held directions, ``sum c_i c_i^T`` over the held local axes, in global axes,
for the translations and for the rotations.
"""

from __future__ import annotations

import pathlib

import numpy as np

import ada

FIXTURE = pathlib.Path(__file__).parents[5] / "files/fem_files/sesam/genie_supports_frames_T1.FEM"


def _records(path):
    recs = []
    for line in pathlib.Path(path).read_text().splitlines():
        if not line.strip():
            continue
        try:
            values = [float(x) for x in line[8:].split()]
        except ValueError:
            values = None
        if line[:8].strip():
            recs.append([line[:8].strip(), values])
        elif recs and recs[-1][1] is not None and values is not None:
            recs[-1][1].extend(values)
    return [(n, v) for n, v in recs if v is not None]


#: Known, and not about frames: the master nodes of three support rigid links, whose BNBCD the round trip loses.
RIGID_LINK_MASTERS = {(2.0, 10.0, 1.0), (2.0, 13.0, 1.0), (2.0, 22.0, 1.0)}


def held_projectors(path) -> dict:
    """``{rounded node position: (P_translations, P_rotations)}`` of every BNBCD node, in global axes."""
    recs = _records(path)
    xyz = {int(v[0]): tuple(round(x, 6) for x in v[1:4]) for n, v in recs if n == "GCOORD"}
    trans = {int(v[0]): np.asarray(v[1:10]).reshape(3, 3, order="F") for n, v in recs if n == "BNTRCOS"}
    frame = {int(v[0]): trans[int(v[1])] for n, v in recs if n == "BNDOF" and int(v[1])}
    out = {}
    for n, v in recs:
        if n != "BNBCD":
            continue
        nid, ndof, codes = int(v[0]), int(v[1]), v[2 : 2 + int(v[1])]
        c = frame.get(nid, np.eye(3))
        held = [code in (1.0, 2.0) for code in codes] + [False] * (6 - ndof)
        pt = sum((np.outer(c[i], c[i]) for i in range(3) if held[i]), np.zeros((3, 3)))
        pr = sum((np.outer(c[i], c[i]) for i in range(3) if held[3 + i]), np.zeros((3, 3)))
        if pt.any() or pr.any():  # a record holding nothing (a spring's or a link's node) says nothing either way
            out[xyz[nid]] = (pt, pr)
    return out


def test_transformed_supports_are_read_into_their_frame():
    fem = ada.from_fem(FIXTURE).get_all_parts_in_assembly()[0].fem
    framed = [bc for bc in fem.bcs if bc.csys is not None]
    assert framed, "the fixture's BNDOF nodes came in without a frame"
    for bc in framed:
        axes = bc.csys.axes()
        assert np.allclose(axes @ axes.T, np.eye(3), atol=1e-6)


def test_every_node_is_held_in_the_same_directions_after_a_round_trip(tmp_path):
    a = ada.from_fem(FIXTURE)
    a.to_fem("rt", "sesam", scratch_dir=tmp_path, overwrite=True)
    want = held_projectors(FIXTURE)
    got = held_projectors(next((tmp_path / "rt").glob("*T1.FEM")))
    # Before the frames were read, 33 of these nodes came back held along the wrong directions. Three rigid-link
    # masters lose their BNBCD record on the way through, frames or none (as before this change): not this test's.
    assert set(want) - set(got) == RIGID_LINK_MASTERS and not set(got) - set(want)
    for pos, (pt, pr) in want.items():
        if pos in RIGID_LINK_MASTERS:
            continue
        assert np.allclose(got[pos][0], pt, atol=1e-6), pos
        assert np.allclose(got[pos][1], pr, atol=1e-6), pos
