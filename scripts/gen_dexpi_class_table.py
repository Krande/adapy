"""Regenerate the vendored DEXPI class table from a local Specification checkout.

The DEXPI Specification (https://gitlab.com/dexpi/Specification, CC BY 4.0) declares its
information model as a pile of Python *scripts* that are executed by the ``dexpi.specificator``
toolchain -- ``PIPING.BallValve = CONCRETE_CLASS(superTypes=[PIPING.OperatedValve], ...)`` and so
on. Nothing in those files is importable without the toolchain, so this generator parses them with
``ast`` and never imports them.

It writes ``src/ada/cadit/dexpi/resources/dexpi_classes.json``: one entry per class, keyed by its
simple name, carrying the package it lives in (``Plant/Piping``), its supertypes, and the RDL
reference the spec declared for it. That table is what lets the reader classify a DEXPI item by
supertype instead of hardcoding several hundred class names.

Regeneration is a deliberate manual act -- this script never fetches anything:

    git clone --depth 1 --branch V2.0.0 https://gitlab.com/dexpi/Specification.git <path-to-spec-checkout>
    python scripts/gen_dexpi_class_table.py --spec-root <path-to-spec-checkout>
"""

from __future__ import annotations

import argparse
import ast
import datetime
import json
import pathlib
import subprocess
import sys

SPEC_REPO_URL = "https://gitlab.com/dexpi/Specification"
SPEC_LICENSE = "CC BY 4.0"

# The packages we care about. ``Process`` is a separate model (process engineering, not P&ID) and
# is deliberately left out -- adding it here is all it would take if that changes.
MODEL_PACKAGES = ("Plant", "Core")

# The specificator's two class-declaring callables.
CLASS_CALLS = {"CONCRETE_CLASS": False, "ABSTRACT_CLASS": True}

# Callables that introduce a package alias at module scope.
PACKAGE_CALLS = ("MODEL", "PACKAGE")

OUT_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "ada" / "cadit" / "dexpi" / "resources"
OUT_PATH = OUT_PATH / "dexpi_classes.json"


def type_string(package: str, name: str) -> str:
    """Build the DEXPI 2.0 ``type`` string for a class in ``package``.

    A DEXPI 2.0 document writes ``type="Plant/Piping.BallValve"``: the imported model prefix, a
    slash, then the dotted path of the class inside that model. So the slash appears exactly once,
    however deeply the class is nested -- ``Core/EngineeringModel``, ``Plant/PlantModel``.
    """
    model, *sub = package.split("/")
    return f"{model}/{'.'.join([*sub, name])}"


class SpecFileParser:
    """Parses one specificator model file into ``{qualified_name: entry}``.

    Package aliases are resolved as the module body is walked, because a file's class assignments
    are written against short aliases declared at its top (``PIPING = Plant.Piping = PACKAGE()``,
    ``EQUIPMENT = Plant.ProcessEquipment``).
    """

    def __init__(self, path: pathlib.Path):
        self.path = path
        # alias name -> package path with "/" separators, e.g. {"PIPING": "Plant/Piping"}
        self.aliases: dict[str, str] = {}
        # model root package -> the URI the MODEL(...) declaration gave it
        self.model_uris: dict[str, str] = {}
        self.classes: dict[str, dict] = {}

    def parse(self) -> None:
        tree = ast.parse(self.path.read_text(encoding="utf-8"), filename=str(self.path))
        for node in tree.body:
            if not isinstance(node, ast.Assign):
                continue
            if self._read_class(node):
                continue
            self._read_alias(node)

    # -- package aliases ------------------------------------------------------------------

    def _resolve_package(self, node: ast.expr) -> str | None:
        """Resolve ``PIPING`` / ``Plant.Piping`` to the package path ``Plant/Piping``."""
        if isinstance(node, ast.Name):
            return self.aliases.get(node.id)
        if isinstance(node, ast.Attribute):
            base = self._resolve_package(node.value)
            if base is not None:
                return f"{base}/{node.attr}"
        return None

    def _read_alias(self, node: ast.Assign) -> None:
        value = node.value
        path: str | None = None

        if isinstance(value, ast.Call) and isinstance(value.func, ast.Name):
            if value.func.id not in PACKAGE_CALLS:
                # TEMPLATE_MODEL and friends are not packages we index; make sure their alias
                # never resolves, so a template reference cannot be mistaken for a class.
                return
            if value.func.id == "MODEL":
                name = self._keyword_str(value, "name")
                uri = self._keyword_str(value, "uri")
                if name is not None:
                    path = name
                    if uri is not None:
                        self.model_uris[name] = uri
        elif isinstance(value, ast.Attribute):
            path = self._resolve_package(value)

        # ``PIPING = Plant.Piping = PACKAGE()`` -- the attribute target names the package, the
        # bare-name target is the alias for it. Resolve the attribute targets first.
        if path is None:
            for target in node.targets:
                if isinstance(target, ast.Attribute):
                    path = self._resolve_package(target)
                    if path is not None:
                        break

        if path is None:
            return

        for target in node.targets:
            if isinstance(target, ast.Name):
                self.aliases[target.id] = path

    @staticmethod
    def _keyword_str(call: ast.Call, name: str) -> str | None:
        for kw in call.keywords:
            if kw.arg == name and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                return kw.value.value
        return None

    # -- class declarations ---------------------------------------------------------------

    def _read_class(self, node: ast.Assign) -> bool:
        value = node.value
        if not isinstance(value, ast.Call) or not isinstance(value.func, ast.Name):
            return False
        if value.func.id not in CLASS_CALLS:
            return False
        if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Attribute):
            return False

        target = node.targets[0]
        package = self._resolve_package(target.value)
        if package is None:
            raise SystemExit(
                f"{self.path}:{node.lineno}: cannot resolve the package of "
                f"{ast.unparse(target)!r} -- the spec's alias conventions have changed"
            )

        name = target.attr
        qualified = type_string(package, name)
        model_root = package.split("/", 1)[0]
        entry = {
            "name": name,
            "package": package,
            "qualified_name": qualified,
            "abstract": CLASS_CALLS[value.func.id],
            "supertypes": self._read_supertypes(value),
            "rdl": self._read_rdl(value),
            "model": model_root,
        }
        self.classes[qualified] = entry
        return True

    def _read_supertypes(self, call: ast.Call) -> list[str]:
        out: list[str] = []
        for kw in call.keywords:
            if kw.arg != "superTypes" or not isinstance(kw.value, ast.List):
                continue
            for item in kw.value.elts:
                if isinstance(item, ast.Attribute):
                    package = self._resolve_package(item.value)
                    out.append(type_string(package, item.attr) if package else ast.unparse(item))
                else:
                    out.append(ast.unparse(item))
        return out

    @staticmethod
    def _read_rdl(call: ast.Call) -> str | None:
        """The spec references RDL classes by symbol (``JORD_RDL.BALL_VALVE``), not by URI.

        The symbol->URI table lives in the ``dexpi.specificator`` package rather than in the
        Specification repo, so the symbol is the most we can honestly record here.
        """
        for kw in call.keywords:
            if kw.arg != "rdl":
                continue
            if isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                return kw.value.value
            return ast.unparse(kw.value)
        return None


def _git(spec_root: pathlib.Path, *args: str) -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", str(spec_root), *args],
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return out.stdout.strip() or None


def collect(spec_root: pathlib.Path) -> tuple[dict[str, dict], dict[str, str], list[pathlib.Path]]:
    model_root = spec_root / "src" / "model"
    looked_at = [model_root / package for package in MODEL_PACKAGES]

    classes: dict[str, dict] = {}
    model_uris: dict[str, str] = {}
    files: list[pathlib.Path] = []
    for package_dir in looked_at:
        files.extend(sorted(package_dir.rglob("*.py")))

    for path in files:
        parser = SpecFileParser(path)
        parser.parse()
        classes.update(parser.classes)
        model_uris.update(parser.model_uris)

    return classes, model_uris, looked_at


def build_table(classes: dict[str, dict], model_uris: dict[str, str]) -> dict[str, dict]:
    """Key the table by simple class name, attaching a model-scoped URI to each entry.

    DEXPI 2.0 identifies a type by the pair (imported model URI, ``type`` string) --
    ``<Import prefix="Plant" source=".../Plant.xml"/>`` plus ``type="Plant/Piping.BallValve"``.
    ``uri`` is that pair written as one string; it is not an RDL URI.
    """
    by_name: dict[str, dict] = {}
    collisions: dict[str, list[str]] = {}

    for qualified, entry in sorted(classes.items()):
        name = entry["name"]
        if name in by_name:
            collisions.setdefault(name, [by_name[name]["qualified_name"]]).append(qualified)
            continue
        model_uri = model_uris.get(entry.pop("model"))
        out = dict(entry)
        out["uri"] = f"{model_uri}#{qualified}" if model_uri else None
        by_name[name] = out

    if collisions:
        detail = "; ".join(f"{name}: {sorted(paths)}" for name, paths in sorted(collisions.items()))
        raise SystemExit(
            "the DEXPI class table is keyed by simple class name, but the spec now declares the "
            f"same name in more than one package -- {detail}"
        )

    return dict(sorted(by_name.items()))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument(
        "--spec-root",
        required=True,
        type=pathlib.Path,
        help="local checkout of gitlab.com/dexpi/Specification",
    )
    ap.add_argument("--tag", default=None, help="override the tag recorded in _meta")
    ap.add_argument("--out", default=OUT_PATH, type=pathlib.Path, help="output JSON path")
    args = ap.parse_args(argv)

    spec_root = args.spec_root.expanduser().resolve()
    classes, model_uris, looked_at = collect(spec_root)
    if not classes:
        raise SystemExit(
            "no DEXPI classes found. Looked for CONCRETE_CLASS/ABSTRACT_CLASS assignments in:\n  "
            + "\n  ".join(str(p / "**" / "*.py") for p in looked_at)
            + f"\n(--spec-root was {spec_root})"
        )

    table = build_table(classes, model_uris)
    tag = args.tag or _git(spec_root, "describe", "--tags", "--always")
    payload = {
        "_meta": {
            "note": "generated - do not edit. Regenerate with scripts/gen_dexpi_class_table.py.",
            "source": SPEC_REPO_URL,
            "tag": tag,
            "commit": _git(spec_root, "rev-parse", "HEAD"),
            "generated": datetime.date.today().isoformat(),
            "license": SPEC_LICENSE,
            "packages": list(MODEL_PACKAGES),
            "class_count": len(table),
        },
        "classes": table,
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8")

    per_package: dict[str, int] = {}
    for entry in table.values():
        per_package[entry["package"]] = per_package.get(entry["package"], 0) + 1
    print(f"wrote {args.out} ({len(table)} classes, {SPEC_REPO_URL} @ {tag})")
    for package, count in sorted(per_package.items()):
        print(f"  {package}: {count}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
