"""The API process is not the worker: no route may import the format handlers.

WHAT BREAKS, AND WHERE IT BREAKS. Routes run in the viewer API image; format handlers run in the
worker. The API image is deliberately slim -- no CAD readers, no kernel -- so importing
``..formats`` from a route pulls the whole handler chain in with it, and the chain imports
``ada.cadit`` for the sources core really does read. The import succeeds in every development
environment and in every test environment, and fails when the slim image starts:

    File "/app/src/ada/comms/rest/formats/clash_check.py", line 25, in <module>
        from ada.cadit.ifc.read.native_members import load_members_or_model
    ModuleNotFoundError: No module named 'ada.cadit'

That is a real failure this gate exists to move earlier: it shipped once, caught by the viewer
image's own smoke test after the build, which is the last place anyone wants to learn it.

WHY A LITERAL IS THE RIGHT ANSWER, not a shared constant module. A job kind is a WIRE value --
the route puts it in a job, the worker matches on it, and the two halves may be different
versions of adapy at the same moment. A constant makes them look coupled while doing nothing to
keep them so; the string is the contract either way. Every route already names its kind this way,
and ``test_format_registry`` is what proves the worker side still answers to it.
"""

from __future__ import annotations

import ast
from pathlib import Path

import ada.comms.rest.routes as routes_pkg

ROUTES = Path(routes_pkg.__file__).resolve().parent


def _module_level_imports(path: Path) -> list[str]:
    """Every module the file imports AT MODULE SCOPE. Imports inside a function are deliberately
    ignored: those run in the process that calls them, which is the sanctioned way for a route to
    reach a heavy dependency it needs only sometimes."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: list[str] = []
    for node in tree.body:  # top level only
        if isinstance(node, ast.Import):
            names += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            # `from ..formats.x import y` -> "..formats.x"; level counts the dots.
            names.append("." * node.level + (node.module or ""))
    return names


def test_no_route_imports_the_format_handlers_at_module_scope():
    files = sorted(ROUTES.glob("*.py"))
    assert files, f"no route modules under {ROUTES} -- a gate that read nothing proves nothing"

    offenders = [
        f"{path.name}: {name}"
        for path in files
        for name in _module_level_imports(path)
        if name.lstrip(".").startswith("formats") or name.startswith("ada.comms.rest.formats")
    ]
    assert offenders == [], (
        "a route imports the worker's format handlers at module scope:\n  "
        + "\n  ".join(offenders)
        + "\n\nThe API image carries no CAD readers, so this import fails when the slim viewer "
        "image starts. Name the job kind as a string literal, as every other route does."
    )


def test_the_gate_would_catch_the_import_it_exists_for(tmp_path):
    """A walker that quietly matched nothing would pass while proving nothing."""
    probe = tmp_path / "probe.py"
    probe.write_text("from ..formats.clash_check_asset import CLASH_CHECK_ASSET_KIND\n", encoding="utf-8")
    assert any(n.lstrip(".").startswith("formats") for n in _module_level_imports(probe))

    inside_a_function = tmp_path / "ok.py"
    inside_a_function.write_text(
        "def run():\n    from ..formats.clash_check_asset import CLASH_CHECK_ASSET_KIND\n", encoding="utf-8"
    )
    assert _module_level_imports(inside_a_function) == []
