"""Core's convex-contact check, complete: what a connection generator needs so it can call core for
every contact check instead of keeping a checker of its own.

* the engine's numpy arrays are read wherever a patch or a point list is (no "truth value of an array
  is ambiguous");
* a member driven into another is kept whatever its area (``keep_penetration_depth``), in the engine
  pass and the annotate step alike, while an edge graze is still dropped;
* the in-process entry points (``ada.clash.mesh_contacts``) and the pass over a member model give the
  contacts the criteria keep, in core's neutral contact, which survives a JSON round trip.
"""

from __future__ import annotations

import json

import numpy as np
import pytest

import ada
from ada.clash.contact_criteria import (
    DEFAULT_KEEP_PENETRATION_DEPTH,
    DEFAULT_MIN_CONTACT_AREA,
    ENGINE_MIN_CONTACT_AREA,
    ContactCriteria,
    engine_min_contact_area,
    is_below_min_area,
    is_edge_contact,
    is_run_in,
    member_shape,
    patch_width,
)
from ada.clash.contacts import annotate_applicable
from ada.clash.convex_contact import patches_incoming_first, pick_incoming
from ada.clash.result import ClashContact
from ada.clash.sidecar import (
    ContactPatch,
    ContactRecord,
    ContactRun,
    parse_run,
    patches_from,
)

STRIP = 1e-3
BOX = "BOX200x200x10x10"
S = 0.5**0.5
#: A BOX200's half diagonal: how far a 45-degree-turned one reaches below its axis.
HALF_DIAGONAL = 0.1 * 2**0.5


# ── numpy patches ───────────────────────────────────────────────────────────────────────────


def _line_patch(make):
    """A 1.2 m line contact's patch, its points built by ``make`` (np.array, list or tuple)."""
    pts = make([[0.0, 0.0, 0.0], [1.2, 0.0, 0.0]])
    return {"normal": make([0.0, 0.0, 1.0]), "points1": pts, "points2": pts}


@pytest.mark.parametrize("make", [np.array, lambda v: [list(p) if isinstance(p, list) else p for p in v], tuple])
def test_patches_are_read_as_arrays_lists_or_tuples(make):
    patches = [_line_patch(make)]
    assert patch_width(1.2 * STRIP, patches) == pytest.approx(STRIP)
    thick = member_shape(ada.Beam("t", (10, 0, 0), (11, 0, 0), BOX))
    assert is_edge_contact(1.2 * STRIP, patches, (5.0, 5.0, 5.0), thick, thick, ratio=0.5)
    (restated,) = patches_incoming_first(patches, flipped=True)
    assert restated["normal"] == [-0.0, -0.0, -1.0]
    assert restated["points_incoming"] == [[0.0, 0.0, 0.0], [1.2, 0.0, 0.0]]
    (patch,) = patches_from(patches)
    assert patch.points1 == ((0.0, 0.0, 0.0), (1.2, 0.0, 0.0))


def test_the_engines_arrays_are_read_everywhere():
    patches = np.empty(1, dtype=object)
    square = np.array([[0, 0, 0], [0.2, 0, 0], [0.2, 0.2, 0], [0, 0.2, 0]], dtype=float)
    patches[0] = {"normal": np.array([0.0, 0.0, 1.0]), "points1": square, "points2": square}
    assert patch_width(0.04, patches) == pytest.approx(0.04 / np.hypot(0.2, 0.2))
    beam = [(np.zeros(3), "start"), (np.array([0.0, 0.0, 1.0]), "end")]
    assert pick_incoming(beam, beam, np.zeros(3), patches=patches) is not None
    contact = ClashContact.from_dict({"near_points": np.array([[0, 0, 0], [0, 0, 1.0]]), "normal": np.ones(3)})
    assert contact.near_points == ((0.0, 0.0, 0.0), (0.0, 0.0, 1.0))


def test_absent_patches_and_points_are_empty():
    assert patch_width(1e-3, None) is None
    assert patch_width(1e-3, [{"points1": None}]) is None
    assert patches_incoming_first(None, flipped=False) == []
    assert patches_from(None) == ()
    assert ClashContact.from_dict({"near_points": None}).near_points == ()


# ── the penetration rule ────────────────────────────────────────────────────────────────────


def test_a_member_driven_in_is_kept_whatever_its_area():
    keep = DEFAULT_KEEP_PENETRATION_DEPTH
    assert keep == pytest.approx(0.002)
    small = 140e-6  # a 140 mm line: 140 mm², under 200 mm²
    assert is_below_min_area(small, 0.0, min_contact_area=DEFAULT_MIN_CONTACT_AREA, keep_penetration_depth=keep)
    assert is_below_min_area(small, -0.001, min_contact_area=DEFAULT_MIN_CONTACT_AREA, keep_penetration_depth=keep)
    assert not is_below_min_area(small, -0.005, min_contact_area=DEFAULT_MIN_CONTACT_AREA, keep_penetration_depth=keep)
    # 0 turns it off; a gap is never a run-in.
    assert is_below_min_area(small, -0.005, min_contact_area=DEFAULT_MIN_CONTACT_AREA, keep_penetration_depth=0.0)
    assert not is_run_in(0.005, keep)
    # With it on, the engine is asked for its floor and the area is judged after it.
    assert engine_min_contact_area(DEFAULT_MIN_CONTACT_AREA, keep) == ENGINE_MIN_CONTACT_AREA
    assert engine_min_contact_area(DEFAULT_MIN_CONTACT_AREA, 0.0) == DEFAULT_MIN_CONTACT_AREA


def test_a_member_driven_in_is_no_graze():
    patch = [{"points1": [(0, 0, 0), (1, 0, 0)]}]
    thick = member_shape(ada.Beam("t", (10, 0, 0), (11, 0, 0), BOX))
    far = (5.0, 5.0, 5.0)
    assert is_edge_contact(1e-3, patch, far, thick, thick, ratio=0.5, distance=0.0, keep_penetration_depth=0.002)
    assert not is_edge_contact(1e-3, patch, far, thick, thick, ratio=0.5, distance=-0.005, keep_penetration_depth=0.002)


def test_the_criteria_from_options_and_from_an_old_run():
    assert ContactCriteria.from_options(None) == ContactCriteria()
    assert ContactCriteria.from_options({"keep_penetration_depth": "0"}).keep_penetration_depth == 0.0
    assert ContactCriteria.from_options({"min_contact_area": -1}).min_contact_area == DEFAULT_MIN_CONTACT_AREA
    # A run measured before a rule existed keeps what it was measured with: the rule is off for it.
    old = ContactCriteria.from_options({"min_contact_area": 2e-4}, of_run=True)
    assert (old.min_contact_width_ratio, old.keep_penetration_depth) == (0.0, 0.0)


def _record(cid, members, points, area, distance) -> ContactRecord:
    pts = tuple(tuple(float(c) for c in p) for p in points)
    centre = tuple(float(c) for c in np.mean(np.asarray(pts), axis=0))
    return ContactRecord(
        id=cid,
        members=members,
        near_points=(centre, centre),
        normal=(0.0, 0.0, 1.0),
        distance=distance,
        contact_area=area,
        patches=(ContactPatch(normal=(0.0, 0.0, 1.0), points1=pts, points2=pts),),
    )


def _annotated(**options) -> list[str]:
    """``a`` along x with two members crossing it mid-span, each along a 140 mm line: ``driven`` 5 mm
    into it, ``resting`` on it."""
    model = ada.Part("p") / [
        ada.Beam("a", (0, 0, 0), (2, 0, 0), BOX),
        ada.Beam("driven", (0.5, -1, 0.2), (0.5, 1, 0.2), BOX),
        ada.Beam("resting", (1.5, -1, 0.2), (1.5, 1, 0.2), BOX),
    ]
    line = 140 * STRIP * 1e-3
    run = ContactRun(
        run_id="r1",
        created_at="2026-01-01T00:00:00Z",
        engine="wasm",
        target={"kind": "file", "source_key": "m.ifc"},
        geometry={"key": "m.glb", "sha256": "0" * 64, "up_axis": "z"},
        options={"security_margin": 0.01, "min_contact_area": DEFAULT_MIN_CONTACT_AREA, **options},
        members=tuple({"id": str(k), "name": n} for k, n in enumerate(["a", "driven", "resting"])),
        contacts=(
            _record("driven", (0, 1), [(0.43, 0, 0.1), (0.57, 0, 0.1)], line, -0.005),
            _record("resting", (0, 2), [(1.43, 0, 0.1), (1.57, 0, 0.1)], line, 0.0),
        ),
    )
    annotated, unbound = annotate_applicable(run, model)
    assert unbound == 0
    return [c.id for c in annotated.contacts]


def test_the_annotate_step_keeps_a_member_driven_in():
    assert _annotated(min_contact_width_ratio=0.5, keep_penetration_depth=0.002) == ["driven"]
    assert _annotated(min_contact_width_ratio=0.5, keep_penetration_depth=0.0) == []
    # A run made before the rule: under the area it was measured with, neither is a contact.
    assert _annotated(min_contact_width_ratio=0.5) == []


def test_a_run_with_the_new_option_survives_its_json():
    run = ContactRun(
        run_id="r1",
        created_at="2026-01-01T00:00:00Z",
        engine="worker",
        target={"kind": "file", "source_key": "m.ifc"},
        geometry={"key": "m.glb", "sha256": "0" * 64, "up_axis": "z"},
        options={"min_contact_area": 2e-4, "min_contact_width_ratio": 0.5, "keep_penetration_depth": 0.002},
        members=({"id": "0", "name": "a"}, {"id": "1", "name": "b"}),
        contacts=(),
    )
    assert parse_run(run.to_json()).options == run.options


# ── in process, on the engine ───────────────────────────────────────────────────────────────

mesh_contacts = pytest.importorskip("ada.clash.mesh_contacts")
needs_engine = pytest.mark.skipif(not mesh_contacts.available(), reason="needs adacpp.clash.find_convex_contacts")


def structure() -> list:
    """Box members, apart from each other in six places:

    * ``girder`` square into the side of ``col`` (a face contact);
    * ``s1`` and ``s2`` spliced end to end (a face contact);
    * ``g1`` and ``g2`` side by side, sharing one edge (a graze);
    * ``brace``, turned 45 degrees, driven 5 mm edge-first into ``low`` (a 140 mm line, 140 mm²);
    * ``rail``, the same, only resting on ``low2`` (140 mm², no overlap);
    * ``stub``, turned 45 degrees, ENDING on ``low3``: a 300 mm line at a member end."""
    driven = 0.07 + HALF_DIAGONAL - 0.005
    resting = 0.07 + HALF_DIAGONAL
    on_low3 = 0.15 + HALF_DIAGONAL
    return [
        ada.Beam("col", (0, 0, 0), (0, 0, 3), BOX),
        ada.Beam("girder", (0.1, 0, 1.5), (2, 0, 1.5), BOX),
        ada.Beam("s1", (0, 3, 0), (1, 3, 0), BOX),
        ada.Beam("s2", (1, 3, 0), (2, 3, 0), BOX),
        ada.Beam("g1", (0, 6, 0), (1, 6, 0), BOX),
        ada.Beam("g2", (0, 6.2, 0.2), (1, 6.2, 0.2), BOX),
        ada.Beam("low", (5, -1, 0), (5, 1, 0), "BOX140x140x10x10"),
        ada.Beam("brace", (4, 0, driven), (6, 0, driven), BOX, up=(0, S, S)),
        ada.Beam("low2", (8, -1, 0), (8, 1, 0), "BOX140x140x10x10"),
        ada.Beam("rail", (7, 0, resting), (9, 0, resting), BOX, up=(0, S, S)),
        ada.Beam("low3", (11, -1, 0), (11, 1, 0), "BOX300x300x10x10"),
        ada.Beam("stub", (10, 0, on_low3), (11.15, 0, on_low3), BOX, up=(0, S, S)),
    ]


#: What the checker keeps of :func:`structure` with its defaults.
KEPT = {("col", "girder"), ("s1", "s2"), ("brace", "low"), ("low3", "stub")}


def _pairs(found) -> set[tuple[str, ...]]:
    return {tuple(sorted(m.name for m in f.members)) for f in found}


@needs_engine
def test_member_contacts_keep_face_contacts_ends_and_members_driven_in_and_drop_grazes():
    result = mesh_contacts.find_member_contacts(structure())
    assert _pairs(result.found) == KEPT
    assert (result.mesh.below_min_area, result.mesh.below_min_width) == (1, 1)  # rail on low2; g1 on g2

    assert _pairs(mesh_contacts.find_member_contacts(structure(), keep_penetration_depth=0.0).found) == KEPT - {
        ("brace", "low")
    }
    assert _pairs(mesh_contacts.find_member_contacts(structure(), min_contact_width_ratio=0.0).found) == KEPT | {
        ("g1", "g2")
    }


@needs_engine
def test_a_member_contact_carries_what_a_generator_reads():
    (found,) = [f for f in mesh_contacts.find_member_contacts(structure()).found if {"brace", "low"} == _names(f)]
    c = found.contact
    assert c.penetration_depth == pytest.approx(0.005, abs=1e-5)
    assert c.contact_area == pytest.approx(140e-6, rel=1e-3)
    assert c.security_margin == pytest.approx(mesh_contacts.DEFAULT_SECURITY_MARGIN)
    assert len(c.near_points) == 2 and c.normal is not None
    assert found.landing is found.members[1]
    assert dict(found.roles) == {found.members[0].name: "incoming", found.members[1].name: "landing"}
    (patch,) = c.extras["patches"]
    assert set(patch) == {"normal", "points_incoming", "points_landing"}
    # The neutral contact, through JSON and back: what a generator rebuilds its own clash from.
    assert ClashContact.from_dict(json.loads(json.dumps(c.to_dict()))) == c


def _names(found) -> set[str]:
    return {m.name for m in found.members}


@needs_engine
def test_mesh_contacts_take_vertices_and_triangles_or_hulls():
    members = structure()[:4]
    hulls = [mesh_contacts.member_mesh(m) for m in members]
    on_hulls = mesh_contacts.find_mesh_contacts(hulls)
    assert {c.members for c in on_hulls.contacts} == {(0, 1), (2, 3)}

    import adacpp.clash

    convex = [adacpp.clash.convex_hull(v) for v in hulls]
    given = mesh_contacts.find_mesh_contacts([v for v, _ in convex], [t for _, t in convex], hull=False)
    assert {c.members for c in given.contacts} == {(0, 1), (2, 3)}
    (c, _) = sorted(given.contacts, key=lambda c: c.members)
    assert c.penetration_depth == pytest.approx(0.0, abs=1e-6) and c.distance <= 1e-6
    assert all(isinstance(p, ContactPatch) for p in c.patches)


@needs_engine
def test_the_pass_over_a_member_model_is_the_same_check():
    from ada.clash.builtin_names import CONVEX_CONTACT_CHECKER, CONVEX_CONTACT_PASS
    from ada.clash.identify import run_clash_check
    from ada.clash.options import ClashOptions

    model = ada.Assembly("a") / (ada.Part("p") / structure())
    result = run_clash_check(model, source_key="test", options=ClashOptions(checker=CONVEX_CONTACT_CHECKER))
    assert {tuple(sorted(m.name for m in j.members)) for j in result.joints} == KEPT
    assert all(j.origin == CONVEX_CONTACT_PASS for j in result.joints)

    off = ClashOptions(checker=CONVEX_CONTACT_CHECKER, checker_options={"keep_penetration_depth": 0})
    result = run_clash_check(model, source_key="test", options=off)
    assert {tuple(sorted(m.name for m in j.members)) for j in result.joints} == KEPT - {("brace", "low")}
