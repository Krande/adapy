"""Convex-contact runs on a viewer GLB (``ada.clash.contacts``) and their hand-off to a builder.

The GLB here is adapy's own export of the model, with the element tables the viewer reads; the run is
measured on it, then bound back to the model by name -- the path a detail job takes.
"""

from __future__ import annotations

import numpy as np
import pytest

import ada
from ada.clash import contacts
from ada.clash.sidecar import parse_run

pytestmark = pytest.mark.skipif(not contacts.available(), reason="needs an adacpp with adacpp.clash.find_glb_contacts")

TARGET = {"kind": "file", "source_key": "frame.ifc"}


def _frame():
    """A BOX column and a BOX girder whose start end is 2 mm into the column's face."""
    column = ada.Beam("col", (0, 0, 0), (0, 0, 3), "BOX300x300x12x12")
    girder = ada.Beam("girder", (0.148, 0, 2.5), (3.0, 0, 2.5), "BOX300x300x12x12")
    return ada.Assembly("a") / (ada.Part("p") / [column, girder])


def _glb(model, tmp_path) -> bytes:
    path = tmp_path / "frame.glb"
    model.to_gltf(path)
    return path.read_bytes()


def _run(model, tmp_path):
    return contacts.check_glb(_glb(model, tmp_path), target=TARGET, glb_key="_derived/frame.ifc.glb")


def test_a_run_on_a_viewer_glb(tmp_path):
    run = _run(_frame(), tmp_path)
    assert run.engine == "worker" and run.geometry["key"] == "_derived/frame.ifc.glb"
    assert len(run.geometry["sha256"]) == 64
    assert {m["name"] for m in run.members} >= {"col", "girder"}
    (c,) = run.contacts
    assert sorted(run.names(c)) == ["col", "girder"]
    assert c.distance == pytest.approx(-0.002, abs=1e-4)  # 2 mm of overlap, coal's sign
    assert c.contact_area > 0 and c.patches
    assert parse_run(run.to_json()) == run


def test_a_clash_binds_back_to_the_members_incoming_first(tmp_path):
    model = _frame()
    run = _run(model, tmp_path)
    (c,) = run.contacts
    by_id, problems = contacts.founds_by_id(run, model, [c.id])
    assert problems == {}
    found = by_id[c.id]
    assert [m.name for m in found.members] == ["girder", "col"]
    assert found.landing.name == "col"
    assert dict(found.roles) == {"girder": "incoming", "col": "landing"}
    assert dict(found.ends) == {"girder": "start"}
    contact = found.contact
    assert contact.penetration_depth == pytest.approx(0.002, abs=1e-4)
    # Incoming -> landing: from the girder's end (+x side) into the column, i.e. towards -x.
    assert np.dot(contact.normal, (-1.0, 0.0, 0.0)) > 0.99
    assert contact.extras["incoming_end"] == "start"
    assert contact.extras["clash_id"] == c.id and contact.extras["run_id"] == run.run_id
    (patch,) = contact.extras["patches"]
    # The incoming polygon is on the girder's end face, the landing one on the column's face.
    assert np.allclose(np.asarray(patch["points_incoming"])[:, 0], 0.148, atol=1e-4)
    assert np.allclose(np.asarray(patch["points_landing"])[:, 0], 0.150, atol=1e-4)


def test_a_member_that_does_not_bind_is_reported_per_clash(tmp_path):
    model = _frame()
    run = _run(model, tmp_path)
    (c,) = run.contacts
    renamed = _frame()
    renamed.get_by_name("girder").name = "something-else"
    by_id, problems = contacts.founds_by_id(run, renamed, [c.id, "not-an-id"])
    assert by_id == {}
    assert "no member named 'girder'" in problems[c.id]
    assert problems["not-an-id"] == "not a clash of this run"


def test_applicable_specs_are_resolved_against_the_rebuilt_members(tmp_path):
    model = _frame()
    run = _run(model, tmp_path)
    annotated, unbound = contacts.annotate_applicable(run, model)
    assert unbound == 0
    assert annotated.provenance["applicability"] == {"resolved": 1, "unbound": 0}
    (c,) = annotated.contacts
    assert isinstance(c.applicable, tuple)
    assert all("spec" in a for a in c.applicable)
    # Round-trips with the specs in it.
    assert parse_run(annotated.to_json()).contacts[0].applicable == c.applicable
