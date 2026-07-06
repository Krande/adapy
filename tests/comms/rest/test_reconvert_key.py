"""The gallery "Re-convert" output lives in a separate ``_reconvert/`` namespace so it never
overwrites a corpus scope's ``_derived/`` audit product."""

import pytest

from ada.comms.rest.converter import (
    UnsupportedFormat,
    derived_key_for,
    is_derived_key,
    is_hidden_key,
    reconvert_key_for,
)


def test_reconvert_key_is_separate_from_derived():
    src = "cad/ifc/beam-standard-case.ifc"
    assert reconvert_key_for(src, "glb") == "_reconvert/cad/ifc/beam-standard-case.ifc.glb"
    # Must NOT collide with the audit-run derived product.
    assert reconvert_key_for(src, "glb") != derived_key_for(src, "glb")


def test_reconvert_key_is_hidden_but_not_derived():
    key = reconvert_key_for("x/y.ifc", "glb")
    # Hidden from the file explorer (throwaway, not a user file)...
    assert is_hidden_key(key) is True
    # ...but NOT a "derived product" — derived-product logic (rename/cleanup/grouping) must not
    # touch it, and the audit ``_derived/`` product stays the canonical one.
    assert is_derived_key(key) is False


def test_reconvert_key_rejects_unknown_format():
    with pytest.raises(UnsupportedFormat):
        reconvert_key_for("x/y.ifc", "bogus")
