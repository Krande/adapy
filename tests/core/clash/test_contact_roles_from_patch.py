"""Which member of a convex contact is INCOMING: the one whose END the contact patch is on.

The nearest-end rule alone gets it wrong where the member that lands is the one with an end near the
contact: a brace cut to run along a post, its end on the post's side a short way from the post's
foot, has the post's foot nearer the contact than its own (uncut) axis end. The post was then taken as
incoming and its foot -- which stands on something else -- welded to the brace. The patch tells them
apart: it lies in a plane along the post's axis (the post's side), and across the brace's.
"""

from __future__ import annotations

import numpy as np
import pytest

import ada
from ada.clash import contacts, convex_contact
from ada.clash.convex_contact import pick_incoming

SECTION = "BOX200x200x10x10"


def _beam_anchors(start, end):
    return [(np.asarray(start, float), "start"), (np.asarray(end, float), "end")]


def _square(centre, u, v, half=0.1):
    c, u, v = (np.asarray(x, float) for x in (centre, u, v))
    return [list(c + half * (su * u + sv * v)) for su, sv in ((-1, -1), (1, -1), (1, 1), (-1, 1))]


# ── the rule, on anchors and patches ─────────────────────────────────────────────────────────


def test_a_brace_end_on_a_posts_side_near_its_foot_is_the_incoming():
    """A vertical post (foot at z 0) and a brace at 45 degrees whose cut end lies on the post's +x side
    at z 0.3: the post's ends are nearer the contact point than the brace's axis end."""
    post = _beam_anchors((0, 0, 0), (0, 0, 0.6))
    brace = _beam_anchors((-0.3, 0, -0.1), (1.0, 0, 1.2))
    near = (0.1, 0.0, 0.3)
    patch = {"normal": (1.0, 0.0, 0.0), "points1": _square((0.1, 0, 0.3), (0, 1, 0), (0, 0, 1))}

    which, _, _ = pick_incoming(post, brace, near)
    assert which == 0, "nearest end alone: the post"
    which, anchor, end = pick_incoming(post, brace, near, patches=[patch])
    assert which == 1 and end == "start", "the patch is on the post's side: the brace's end is on it"
    assert np.allclose(anchor, (-0.3, 0, -0.1))


def test_a_horizontal_members_mitred_end_on_an_inclined_braces_side_is_the_incoming():
    """A horizontal member's end cut at 39 degrees to its axis lies on the side of a brace rising at 52
    degrees, a little above the brace's foot, which is the nearer anchor."""
    brace = _beam_anchors((0, 0.2, 0.1), (0, 0.2 + 0.611 * 1.2, 0.1 + 0.791 * 1.2))
    member = _beam_anchors((0, 0.4, 0.26), (0, 3.6, 0.26))
    normal = np.array([0.0, 0.791, -0.611])  # square to the brace's axis: on its side
    centre = np.array([0.0, 0.24, 0.19])
    patch = {"normal": normal, "points1": _square(centre, (1, 0, 0), np.cross(normal, (1, 0, 0)))}

    assert pick_incoming(brace, member, centre)[0] == 0, "nearest end alone: the brace"
    which, _, end = pick_incoming(brace, member, centre, patches=[patch])
    assert which == 1 and end == "start"


def test_a_stub_cut_on_an_inclined_member_stays_incoming():
    """A short stub whose end is cut on the side of an inclined member: already the incoming by the
    nearest end, and the patch (along the inclined member's axis, 32 degrees off the stub's) agrees."""
    stub = _beam_anchors((0, 0.68, 0.21), (0, 0.88, 0.21))
    inclined = _beam_anchors((0, 0.95, 0.9), (0, -1.23, -2.64))
    normal = np.array([0.0, -0.85, 0.53])
    centre = np.array([0.0, 0.65, 0.21])
    patch = {"normal": normal, "points1": _square(centre, (1, 0, 0), np.cross(normal, (1, 0, 0)))}
    assert pick_incoming(stub, inclined, centre)[0] == 0
    assert pick_incoming(stub, inclined, centre, patches=[patch])[0] == 0


@pytest.mark.parametrize(
    "case",
    ["line contact", "plate", "side by side", "end to end"],
)
def test_where_the_patch_does_not_decide_the_nearest_end_does(case):
    near = (0.1, 0.0, 0.3)
    post = _beam_anchors((0, 0, 0), (0, 0, 0.6))
    other = _beam_anchors((-0.3, 0, -0.1), (1.0, 0, 1.2))
    patch = {"normal": (1.0, 0.0, 0.0), "points1": _square((0.1, 0, 0.3), (0, 1, 0), (0, 0, 1))}
    if case == "line contact":
        patch = {"normal": (1.0, 0.0, 0.0), "points1": [[0.1, -0.1, 0.3], [0.1, 0.1, 0.3]]}
    elif case == "plate":
        c = np.array([0.5, 0.0, 0.3])
        other = [(c, None), (c, None)]
    elif case == "side by side":
        other = _beam_anchors((0.2, 0, 0.0), (0.2, 0, 2.0))
    elif case == "end to end":
        other = _beam_anchors((0, 0, 0.6), (0, 0, 2.0))
        patch = {"normal": (0.0, 0.0, 1.0), "points1": _square((0, 0, 0.6), (1, 0, 0), (0, 1, 0))}
    assert pick_incoming(post, other, near, patches=[patch]) == pick_incoming(post, other, near)


# ── end to end: a run over the GLB, bound back to its members ────────────────────────────────


@pytest.mark.skipif(not convex_contact.available(), reason="needs an adacpp that measures a viewer GLB")
def test_a_run_names_the_brace_incoming_where_its_cut_end_is_on_the_posts_side(tmp_path):
    post = ada.Beam("post", (0, 0, 0), (0, 0, 0.6), SECTION)
    d = np.array([1.0, 0.0, 1.0]) / np.sqrt(2)
    brace = ada.Beam("brace", (-0.3, 0, -0.1), tuple(np.array([-0.3, 0, -0.1]) + 1.8 * d), SECTION)
    brace.add_boolean(ada.BoolHalfSpace(ada.Point(0.1, 0, 0), ada.Direction(-1, 0, 0)))
    model = ada.Assembly("a") / (ada.Part("p") / [post, brace])
    glb = tmp_path / "m.glb"
    model.to_gltf(glb)
    run = contacts.check_glb(glb.read_bytes(), target={"kind": "file", "source_key": "m.ifc"}, glb_key="m.glb")
    (record,) = run.contacts
    assert any(len(p.points1) >= 3 for p in record.patches), "the brace's cut end lies on the post's side"
    by_id, problems = contacts.founds_by_id(run, model, [record.id])
    assert problems == {}
    found = by_id[record.id]
    assert dict(found.roles) == {"brace": "incoming", "post": "landing"}
    assert dict(found.ends) == {"brace": "start"}
    assert found.landing is post
