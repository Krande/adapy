"""Core's convex-contact checker: solids that touch or overlap, measured on the viewer GLB by adacpp.

Pins what makes it safe as a checker of its own -- it runs only when picked (a default check is core's
three passes, exactly as before), it is attributed to no pool, and it refuses to run as a pass over a
member model -- and that what it measures reaches a builder incoming-first, in core's vocabulary: a run
over the GLB adapy writes for a model, bound back to that model's members.
"""

from __future__ import annotations

import numpy as np
import pytest

import ada
from ada.clash import contacts, convex_contact
from ada.clash.builtin_names import CONVEX_CONTACT_CHECKER, CONVEX_CONTACT_PASS
from ada.clash.identify import run_clash_check
from ada.clash.options import ClashOptions
from ada.clash.passes import get_pass, list_checkers, passes_for, selected_passes

pytestmark = pytest.mark.skipif(
    not convex_contact.available(), reason="needs an adacpp that measures a viewer GLB (adacpp.clash)"
)

IPE200_FOOTPRINT = 0.2 * 0.1  # h x b: the convex hull of the section, which is what is measured


def _deck_and_column(column_bottom_z: float):
    """A 10 mm deck plate (z 0..0.01) and an IPE200 column standing on it, away from its centroid."""
    plate = ada.Plate("deck", [(0, 0), (2, 0), (2, 2), (0, 2)], 0.01)
    column = ada.Beam("col", (0.5, 0.5, column_bottom_z), (0.5, 0.5, 3.0), "IPE200")
    return ada.Assembly("a") / (ada.Part("p") / [column, plate])


def _measure(model, tmp_path, **options):
    """A run over the GLB adapy writes for ``model``, and each clash bound back to its members."""
    glb = tmp_path / "m.glb"
    model.to_gltf(glb)
    run = contacts.check_glb(
        glb.read_bytes(), target={"kind": "file", "source_key": "m.ifc"}, glb_key="m.glb", options=options
    )
    by_id, problems = contacts.founds_by_id(run, model, [c.id for c in run.contacts])
    assert problems == {}
    return [by_id[c.id] for c in run.contacts]


def test_registered_as_a_checker_of_its_own():
    p = get_pass(CONVEX_CONTACT_PASS)
    assert p is not None and p.checker == CONVEX_CONTACT_CHECKER and p.capability is None
    assert [q.name for q in passes_for(CONVEX_CONTACT_CHECKER)] == [CONVEX_CONTACT_PASS]
    # Not one of core's default searches: a default check is unchanged by its being installed.
    assert CONVEX_CONTACT_PASS not in {q.name for q in selected_passes(None)}
    entry = {c["name"]: c for c in list_checkers()}[CONVEX_CONTACT_CHECKER]
    assert entry["capability"] is None
    assert {o["key"] for o in entry["options"]} == {"security_margin", "min_contact_area"}


def test_a_default_check_does_not_run_it():
    result = run_clash_check(_deck_and_column(0.005), source_key="test")
    ran = {p["name"]: p for p in result.passes}
    assert ran[CONVEX_CONTACT_PASS]["ran"] is False
    assert all(j.origin != CONVEX_CONTACT_PASS for j in result.joints)


def test_it_refuses_to_run_as_a_pass_over_a_member_model():
    result = run_clash_check(
        _deck_and_column(0.005), source_key="test", options=ClashOptions(checker=CONVEX_CONTACT_CHECKER)
    )
    (entry,) = result.passes
    assert entry["ran"] is False and "viewer GLB" in entry["reason"]
    assert result.joints == ()


def test_column_standing_in_a_deck_is_one_contact_written_incoming_first(tmp_path):
    (found,) = _measure(_deck_and_column(0.005), tmp_path)
    assert [m.name for m in found.members] == ["col", "deck"]
    assert dict(found.roles) == {"col": "incoming", "deck": "landing"}
    assert dict(found.ends) == {"col": "start"}
    assert found.landing.name == "deck"

    c = found.contact
    # 5 mm into a deck whose top is at z = 0.01: an overlap of 5 mm, reported positive.
    assert c.penetration_depth == pytest.approx(0.005, abs=1e-5)
    # Incoming -> landing: from the column down into the deck.
    assert np.allclose(c.normal, (0, 0, -1), atol=1e-6)
    assert c.near_points[0][2] == pytest.approx(0.005, abs=1e-5)  # on the column: its bottom
    assert c.near_points[1][2] == pytest.approx(0.01, abs=1e-5)  # on the deck: its top
    assert c.contact_area == pytest.approx(IPE200_FOOTPRINT, rel=1e-4)
    assert c.security_margin == pytest.approx(convex_contact.DEFAULT_SECURITY_MARGIN)
    # The column's contact end points along the normal, straight into the deck.
    assert c.incoming_angle_deg == pytest.approx(0.0, abs=1e-4)
    assert c.extras["incoming_end"] == "start"
    # The contact-patch polygon, incoming-first: the column's footprint at the column's bottom.
    (patch,) = c.extras["patches"]
    pts = np.asarray(patch["points_incoming"])
    assert np.allclose(pts[:, 2], 0.005, atol=1e-5)
    assert np.ptp(pts[:, 0]) * np.ptp(pts[:, 1]) == pytest.approx(IPE200_FOOTPRINT, rel=1e-4)


def test_the_security_margin_is_the_checks_option(tmp_path):
    # A 5 mm gap: inside the default 10 mm margin, outside a 1 mm one.
    model = _deck_and_column(0.015)
    assert len(_measure(model, tmp_path)) == 1
    assert _measure(model, tmp_path, security_margin=0.001) == []


def test_members_apart_are_no_contact(tmp_path):
    assert _measure(_deck_and_column(0.5), tmp_path) == []
