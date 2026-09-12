"""Every relative import in ``ada`` names a module that exists on disk.

A lazy ``from .x import y`` inside a function is not executed by importing the module, so a
package split that moves ``x`` one level up or down leaves a failure that only fires at runtime.
That is how a worker came to crash at boot after ``worker.py`` became a package:
``from .utility import`` began naming ``ada.comms.rest.worker.utility`` instead of
``ada.comms.rest.utility``. This walks the AST of every module and resolves each relative import
against the source tree, without importing anything, so the mistake fails here rather than in a
running deployment and regardless of which optional dependencies the test environment carries.
"""

from __future__ import annotations

import ast
import pathlib

import ada

ADA_ROOT = pathlib.Path(ada.__file__).parent


def _module_exists(parts: list[str]) -> bool:
    base = ADA_ROOT.parent.joinpath(*parts)
    return base.with_suffix(".py").is_file() or (base / "__init__.py").is_file()


def _resolves(package: list[str], node: ast.ImportFrom) -> bool:
    up = node.level - 1
    if up > len(package):
        return False
    base = package[: len(package) - up] if up else package
    target = base + (node.module.split(".") if node.module else [])
    if _module_exists(target):
        return True
    # ``from .pkg import name`` may name submodules rather than attributes.
    names = [alias.name for alias in node.names if alias.name != "*"]
    return bool(names) and all(_module_exists(target + [n]) for n in names)


def test_every_relative_import_resolves():
    unresolved: list[str] = []
    for path in sorted(ADA_ROOT.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        package = list(path.relative_to(ADA_ROOT.parent).with_suffix("").parts)[:-1]
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level and not _resolves(package, node):
                rel = path.relative_to(ADA_ROOT.parent)
                unresolved.append(f"{rel}:{node.lineno}: from {'.' * node.level}{node.module or ''} import ...")
    assert not unresolved, "unresolvable relative imports:\n" + "\n".join(unresolved)
