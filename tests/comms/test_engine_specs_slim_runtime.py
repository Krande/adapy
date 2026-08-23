"""The engine-spec predicate must be usable without ``ada.topo_model``.

The REST API runs in a slim runtime that ships only ``ada.comms``, ``ada.cad``,
``ada.config`` and part of ``ada.sections`` (see ``deploy/Dockerfile.viewer``).
``ada.topo_model`` is absent there and cannot be added: importing any of its
submodules executes its ``__init__``, which pulls in the whole modelling stack.

An endpoint that imported the predicate from ``ada.topo_model`` therefore raised
``ModuleNotFoundError`` in the deployed viewer -- a 500 on
``/scopes/{scope}/procedural-engines``, which is precisely the endpoint that
surfaces engines a live worker advertises. These tests pin the arrangement that
fixes it.
"""

import subprocess
import sys

from ada.comms.engine_specs import is_offerable

OFFERABLE = {"slug": "demo", "name": "Demo", "entrypoint": "pkg.mod:compile"}


def test_a_spec_with_name_and_entrypoint_is_offerable():
    assert is_offerable(OFFERABLE) is True


def test_capability_flags_alone_are_not_offerable():
    """Older workers announce flags without a name or entrypoint.

    Offering one of those would present an engine the viewer cannot dispatch to.
    """
    assert is_offerable({"slug": "demo", "supports_grouping": True}) is False


def test_name_without_entrypoint_is_not_offerable():
    assert is_offerable({"slug": "demo", "name": "Demo"}) is False


def test_entrypoint_without_name_is_not_offerable():
    assert is_offerable({"slug": "demo", "entrypoint": "pkg.mod:compile"}) is False


def test_empty_strings_are_treated_as_absent():
    assert is_offerable({"name": "", "entrypoint": "pkg.mod:compile"}) is False
    assert is_offerable({"name": "Demo", "entrypoint": ""}) is False


def test_importing_the_predicate_does_not_drag_in_topo_model():
    """The slim-runtime contract, checked in a clean interpreter.

    If this regresses, the viewer image raises ModuleNotFoundError at request
    time rather than at import time -- i.e. it fails in production, not in CI.
    """
    code = (
        "import sys;"
        "import ada.comms.engine_specs as m;"
        "assert m.is_offerable({'name': 'n', 'entrypoint': 'e'});"
        "assert 'ada.topo_model' not in sys.modules, "
        "'ada.comms.engine_specs pulled in ada.topo_model';"
        "print('ok')"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert "ok" in proc.stdout


def test_engine_catalog_still_exports_the_same_object():
    """Existing importers keep working, and there is only one definition."""
    from ada.topo_model.engine_catalog import is_offerable as from_catalog

    assert from_catalog is is_offerable
