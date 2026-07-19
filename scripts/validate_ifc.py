"""Official buildingSMART-aligned IFC validation for adapy-produced files.

Runs the offline, pip/conda-installable parts of the buildingSMART IFC
Validation Service (https://github.com/buildingSMART/validate) against one or
more IFC files (or directories of IFCs):

* **Syntax check** — the file is opened with ``ifcopenshell.open`` and any
  low-level STEP/parse errors reported by the IfcOpenShell C++ core are
  captured (mirrors the service's "Syntax Check").
* **Schema check** — attribute cardinality/type/selects and inverse
  attributes are checked by ``ifcopenshell.validate`` (mirrors "Schema
  Check").
* **Express/where-rules** — the schema's WHERE rules and global rules are
  evaluated with ``express_rules=True`` (part of "Schema Check").
* **IDS check** (optional) — when ``--ids FILE.ids`` is supplied, ``ifctester``
  validates the model against an Information Delivery Specification.

The service's third pillar, the **Gherkin normative rules**
(``buildingSMART/ifc-gherkin-rules``), is intentionally NOT run here: it is a
git repository of behave feature files rather than a pip package, and it drags
in a heavy closure (Django, pyproj, rtree, shapely, ...). See the module notes
and the accompanying task documentation for how to run it separately.

Exit code is non-zero if any file produces validation errors, so this is
usable as a CI gate. Run via ``pixi run -e ifc-validation ifc-validate <path>``.
"""

from __future__ import annotations

import argparse
import pathlib
import sys
from typing import Iterable

import ifcopenshell
import ifcopenshell.validate


def _iter_ifc_files(paths: Iterable[str]) -> list[pathlib.Path]:
    out: list[pathlib.Path] = []
    for p in paths:
        path = pathlib.Path(p)
        if path.is_dir():
            out.extend(sorted(path.rglob("*.ifc")))
        else:
            out.append(path)
    # de-duplicate while preserving order
    seen: set[pathlib.Path] = set()
    uniq: list[pathlib.Path] = []
    for path in out:
        rp = path.resolve()
        if rp not in seen:
            seen.add(rp)
            uniq.append(path)
    return uniq


def _validate_file(path: pathlib.Path, express_rules: bool) -> list[dict]:
    """Return a list of error statements ({'level','message',...}) for one IFC."""
    logger = ifcopenshell.validate.json_logger()
    try:
        f = ifcopenshell.open(str(path))
    except Exception as exc:  # syntax / unreadable file
        return [{"level": "error", "message": f"Could not open file: {exc}", "type": "syntax"}]

    # Capture low-level C++ parse/geometry errors (syntax pillar).
    try:
        ifcopenshell.validate.log_internal_cpp_errors(f, str(path), logger)
    except Exception as exc:  # pragma: no cover - defensive
        logger.error("internal error running syntax check: %s", exc)

    # Schema + express/where-rule check.
    try:
        ifcopenshell.validate.validate(f, logger, express_rules=express_rules)
    except Exception as exc:  # pragma: no cover - defensive
        logger.error("internal error running schema check: %s", exc)

    return [s for s in logger.statements if s.get("level") == "error"]


def _validate_ids(path: pathlib.Path, ids_path: pathlib.Path) -> tuple[bool, str]:
    """Validate one IFC against an IDS. Returns (passed, summary)."""
    try:
        import ifctester
        import ifctester.ids
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "ifctester is not installed. Install it (IDS pillar is optional) with:\n"
            "    pixi run -e ifc-validation python -m pip install --no-deps ifctester"
        ) from exc

    ids = ifctester.ids.open(str(ids_path))
    model = ifcopenshell.open(str(path))
    ids.validate(model)
    total = failed = 0
    for spec in ids.specifications:
        for req in spec.requirements:
            for res in getattr(req, "failed_entities", []):
                failed += 1
            total += 1
    passed = all(spec.status is not False for spec in ids.specifications)
    n_failed_specs = sum(1 for spec in ids.specifications if spec.status is False)
    return passed, f"{n_failed_specs} of {len(ids.specifications)} specification(s) failed"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="+", help="IFC file(s) and/or directory(ies) to validate.")
    ap.add_argument(
        "--no-rules",
        action="store_true",
        help="Skip express/where-rules; run syntax + schema attribute checks only (faster).",
    )
    ap.add_argument("--ids", default=None, help="Optional IDS file to additionally validate against (ifctester).")
    ap.add_argument("--max-errors", type=int, default=10, help="Max error lines to print per file (default 10).")
    args = ap.parse_args(argv)

    files = _iter_ifc_files(args.paths)
    if not files:
        print("No .ifc files found in the given path(s).", file=sys.stderr)
        return 2

    ids_path = pathlib.Path(args.ids) if args.ids else None
    any_fail = False
    print(f"Validating {len(files)} IFC file(s) with ifcopenshell {ifcopenshell.version}\n")

    for path in files:
        errors = _validate_file(path, express_rules=not args.no_rules)
        status = "PASS" if not errors else "FAIL"
        if errors:
            any_fail = True
        print(f"[{status}] {path}  ({len(errors)} error(s))")
        for stmt in errors[: args.max_errors]:
            msg = " ".join(str(stmt.get("message", "")).split())
            attr = stmt.get("attribute") or stmt.get("type") or ""
            head = f"  - {attr}: " if attr else "  - "
            print(f"{head}{msg[:400]}")
        if len(errors) > args.max_errors:
            print(f"  ... {len(errors) - args.max_errors} more error(s) suppressed")

        if ids_path is not None:
            try:
                ok, summary = _validate_ids(path, ids_path)
            except Exception as exc:
                ok, summary = False, f"ifctester error: {exc}"
            print(f"  [IDS {'PASS' if ok else 'FAIL'}] {summary}")
            if not ok:
                any_fail = True
        print()

    print("=" * 60)
    print("RESULT:", "FAIL" if any_fail else "PASS", f"({len(files)} file(s) checked)")
    return 1 if any_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
