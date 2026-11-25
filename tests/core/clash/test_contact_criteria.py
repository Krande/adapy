"""What core's convex-contact check counts as a contact (``ada.clash.contact_criteria``).

Synthetic runs, as the engine would report them, annotated against the members they name: an edge
graze between two box members is dropped, a face-to-face contact is kept, and so is a member end
running into another even where its patch is only a graze. No adacpp needed: the width criterion is
applied when a run is annotated.
"""

from __future__ import annotations

import numpy as np
import pytest

import ada
from ada.clash.contact_criteria import (
    DEFAULT_MIN_CONTACT_AREA,
    DEFAULT_MIN_CONTACT_WIDTH_RATIO,
    FALLBACK_THICKNESS,
    is_edge_contact,
    member_shape,
    mesh_shape,
    patch_width,
)
from ada.clash.contacts import annotate_applicable
from ada.clash.sidecar import ContactPatch, ContactRecord, ContactRun

#: The engine's line-contact strip [m]: a line contact's area is its length times this.
STRIP = 1e-3
BOX = "BOX200x200x10x10"


def _record(cid: str, members: tuple[int, int], points, area: float, normal=(0.0, 0.0, 1.0)) -> ContactRecord:
    pts = tuple(tuple(float(c) for c in p) for p in points)
    centre = tuple(float(c) for c in np.mean(np.asarray(pts), axis=0))
    return ContactRecord(
        id=cid,
        members=members,
        near_points=(centre, centre),
        normal=normal,
        distance=-1e-4,
        contact_area=area,
        patches=(ContactPatch(normal=normal, points1=pts, points2=pts),),
    )


def _run(records, names, **options) -> ContactRun:
    return ContactRun(
        run_id="r1",
        created_at="2026-01-01T00:00:00Z",
        engine="worker",
        target={"kind": "file", "source_key": "m.ifc"},
        geometry={"key": "m.glb", "sha256": "0" * 64, "up_axis": "z"},
        options={"security_margin": 0.01, "min_contact_area": DEFAULT_MIN_CONTACT_AREA, **options},
        members=tuple({"id": str(k), "name": n} for k, n in enumerate(names)),
        contacts=tuple(records),
    )


@pytest.fixture
def model():
    """Box members 200 x 200, 10 mm walls: ``a`` along x (top face z = 0.1), ``side`` beside it along
    the same line (touching ``a`` along an edge only), ``top`` stacked on it, and ``post`` standing on
    ``a`` with its end on ``a``'s top face."""
    a = ada.Beam("a", (0, 0, 0), (2, 0, 0), BOX)
    side = ada.Beam("side", (0, 0.2, 0.2), (2, 0.2, 0.2), BOX)
    top = ada.Beam("top", (0, 0, 0.2), (2, 0, 0.2), BOX)
    post = ada.Beam("post", (1, 0, 0.1), (1, 0, 2.0), BOX)
    return ada.Part("p") / [a, side, top, post]


def _contacts(model, **options) -> list[str]:
    graze = _record("graze", (0, 1), [(0.4, 0.1, 0.1), (1.6, 0.1, 0.1)], 1.2 * STRIP)  # 1 200 mm², 1 mm wide
    face = _record("face", (0, 2), [(0.4, -0.1, 0.1), (1.6, -0.1, 0.1), (1.6, 0.1, 0.1), (0.4, 0.1, 0.1)], 1.2 * 0.2)
    end = _record("end", (0, 3), [(0.9, -0.1, 0.1), (1.1, -0.1, 0.1)], 0.2 * STRIP)  # the post's end edge
    run = _run([graze, face, end], ["a", "side", "top", "post"], **options)
    annotated, unbound = annotate_applicable(run, model)
    assert unbound == 0
    return [c.id for c in annotated.contacts]


def test_an_edge_graze_is_dropped_and_a_face_contact_and_a_member_end_are_kept(model):
    assert _contacts(model, min_contact_width_ratio=DEFAULT_MIN_CONTACT_WIDTH_RATIO) == ["face", "end"]


def test_a_run_without_the_ratio_keeps_every_contact(model):
    # A run measured before the criterion existed, or one that turns it off.
    assert _contacts(model) == ["graze", "face", "end"]
    assert _contacts(model, min_contact_width_ratio=0.0) == ["graze", "face", "end"]


def test_the_width_is_the_area_over_the_longest_chord():
    assert patch_width(1.2 * STRIP, [{"points1": [(0, 0, 0), (1.2, 0, 0)]}]) == pytest.approx(STRIP)
    square = [{"points1": [(0, 0, 0), (0.2, 0, 0), (0.2, 0.2, 0), (0, 0.2, 0)]}]
    assert patch_width(0.04, square) == pytest.approx(0.04 / np.hypot(0.2, 0.2))
    assert patch_width(1e-3, [{"points1": [(0, 0, 0)]}]) is None


def test_the_thickness_is_the_thinner_wall_else_a_plate_mesh_else_the_fallback():
    box = member_shape(ada.Beam("b", (0, 0, 0), (1, 0, 0), "BOX200x100x8x12"))
    assert box.thickness == pytest.approx(0.008)
    plate = member_shape(ada.Plate("pl", [(0, 0), (1, 0), (1, 1), (0, 1)], 0.02))
    assert plate.thickness == pytest.approx(0.02)

    # A plate's mesh tells its thickness; a beam's does not tell its wall.
    slab = np.array([[x, y, z] for x in (0, 1) for y in (0, 0.8) for z in (0, 0.015)], dtype=float)
    assert mesh_shape(slab).thickness == pytest.approx(0.015)
    bar = np.array([[x, y, z] for x in (0, 3) for y in (0, 0.2) for z in (0, 0.2)], dtype=float)
    assert mesh_shape(bar).thickness is None and len(mesh_shape(bar).ends) == 2

    # 3 mm wide: kept against 4 mm walls (> 2 mm), dropped against 10 mm ones (< 5 mm).
    patch = [{"points1": [(0, 0, 0), (1, 0, 0)]}]
    far = (5.0, 5.0, 5.0)
    assert not is_edge_contact(3e-3, patch, far, None, None, ratio=0.5)  # the fallback, FALLBACK_THICKNESS
    assert FALLBACK_THICKNESS == pytest.approx(0.004)
    thick = member_shape(ada.Beam("t", (10, 0, 0), (11, 0, 0), BOX))
    assert is_edge_contact(3e-3, patch, far, thick, thick, ratio=0.5)
