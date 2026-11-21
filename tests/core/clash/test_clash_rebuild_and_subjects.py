"""The public rebuild of a run's members (``ada.clash.rebuild``), ``build_joint`` and the subject
converter registry (``ada.clash.subjects``)."""

from __future__ import annotations

import pytest

from ada.clash import rebuild
from ada.clash.detail import JointNotBuilt, build_joint
from ada.clash.subjects import (
    register_subject_converter,
    subject_converter,
    unregister_subject_converter,
    weld_assembly_for_connection,
)


def test_the_rebuild_is_public_and_the_old_names_still_resolve():
    pytest.importorskip("fastapi")
    from ada.comms.rest.formats import clash_contacts as cc

    assert cc._rebuild_members is rebuild.rebuild_run_members
    assert cc._check_geometry_unchanged is rebuild.check_geometry_unchanged
    assert cc.load_target_model is rebuild.load_target_model
    assert issubclass(cc.ContactsJobError, rebuild.RebuildError)


def test_a_changed_glb_is_refused():
    class _Run:
        parts = ()
        geometry = {"key": "g.glb", "sha256": "0" * 64}

    class _Storage:
        def get_bytes(self, key):
            return b"other bytes"

    with pytest.raises(rebuild.RebuildError, match="changed since it was checked"):
        rebuild.check_geometry_unchanged(_Run(), _Storage())


def test_a_converter_is_chosen_by_capability_and_given_the_source():
    seen = []

    def mine(conn, *, found=None, spec=None):
        seen.append((conn, spec))
        return {"schema": "ada-weld-assembly/1", "source": {"kind": "mine"}}

    register_subject_converter("cap-x", mine)
    try:
        assert subject_converter("cap-x") is mine
        assert subject_converter("other") is not mine
        doc = weld_assembly_for_connection("conn", capability="cap-x", spec="s", source={"ck": "c" * 16})
        assert doc["source"] == {"kind": "mine", "ck": "c" * 16} and seen == [("conn", "s")]
    finally:
        unregister_subject_converter("cap-x")
    assert subject_converter("cap-x") is not mine


def test_build_joint_says_why_nothing_was_built():
    class _Spec:
        name = "only"
        capability = "cap-none"
        priority = 0
        roles = ()

    class _Reg:
        spec = _Spec()

        @staticmethod
        def fn(**kw):  # pragma: no cover - never reached: the roles do not bind
            raise AssertionError

    class _Found:
        members = ()
        centre = (0.0, 0.0, 0.0)
        contact = None
        landing = None
        origin = "x"

    with pytest.raises(JointNotBuilt) as err:
        build_joint(_Reg(), _Found(), joint_id="j1")
    assert err.value.declined and err.value.declined[0]["spec"] == "only"
