"""The misroute guard exempts every sourceless job kind the registry knows.

The guard once kept its own list of synthetic kinds and missed ``procedural_engine_build``,
whose synthetic key carries no extension, so every engine-build job was failed as misrouted on
every pool. The exemptions now come from the registry, so this pins that every registered
synthetic handler is exempt and that a real extension mismatch is still caught.
"""

from __future__ import annotations

import pytest

import ada.comms.rest.formats as formats
from ada.comms.rest.formats.registry import synthetic_kinds
from ada.comms.rest.worker.routing import misroute_reason

POOL = {"cap": "base", "source_ext_set": {".ifc", ".step"}, "ext_allow_set": None}


def test_every_registered_synthetic_kind_is_exempt():
    kinds = synthetic_kinds()
    assert "procedural_engine_build" in kinds
    assert "component_build" in kinds
    assert "plugin_job" in kinds
    for kind in kinds:
        assert misroute_reason(kind, "_synthetic/engine-build/abc", **POOL) is None, kind


def test_synthetic_kinds_match_the_handlers_that_need_no_source():
    expected = {h.kind for h in formats.handlers() if not h.needs_source}
    assert synthetic_kinds() == frozenset(expected)


def test_an_engine_build_job_is_not_misrouted_on_any_pool():
    reason = misroute_reason(
        "procedural_engine_build",
        "_synthetic/engine-build/engine-1/r3",
        cap="pm-engine",
        source_ext_set=set(),
        ext_allow_set=None,
    )
    assert reason is None


@pytest.mark.parametrize("key", ["scope/model.ifc", "scope/deep/dir/model.STEP"])
def test_a_supported_source_extension_passes(key):
    assert misroute_reason("glb", key, **POOL) is None


def test_an_unsupported_source_extension_is_named_in_the_reason():
    reason = misroute_reason("glb", "scope/model.zzz", **POOL)
    assert reason is not None
    assert "misrouted" in reason
    assert ".zzz" in reason
    assert "'base'" in reason


def test_a_legacy_extension_is_allowed_only_when_the_pool_allows_it():
    from ada.comms.rest.converter import LEGACY_CONVERT_EXTS

    legacy = sorted(LEGACY_CONVERT_EXTS)[0]
    key = f"scope/model{legacy}"
    assert misroute_reason("glb", key, cap="base", source_ext_set=set(), ext_allow_set=None) is None
    assert misroute_reason("glb", key, cap="base", source_ext_set=set(), ext_allow_set={".zzz"}) is not None
