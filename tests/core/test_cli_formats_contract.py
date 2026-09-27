"""``ada_cli.formats`` duplicates what ``ada`` knows about FEM formats. This keeps them in step.

``ada_cli`` builds its parser without importing ``ada`` — that is the whole reason the package
exists outside ``ada`` (see ``ada_cli.main``'s docstring), and argparse needs the format names at
parse time for ``choices=``. So the CLI's tables are a hand-written copy of
``ada.fem.formats.general.get_fem_imports`` / ``get_fem_exports`` and
``ada.fem.formats.utils.interpret_fem_format_from_path``, and nothing but this module stops the
copy from rotting: add a writer to adapy and the CLI silently keeps refusing it; rename one and
``--to`` silently keeps offering a choice that cannot be honoured.

Set equality alone would catch that but would report it as ``{...} != {...}`` over two nine-element
sets, so every check here names the format and the side it is missing from.

The test with real teeth is :func:`test_primary_pattern_matches_what_each_writer_produces`: it runs
each FEM writer and looks for the file ``FEM_WRITE_PRIMARY`` promised. That table is not derivable
from anything in ``ada`` — ``default_fem_inp_path`` disagrees with the USFOS and Code_Aster writers
— so it was read off the writer sources, and a writer changing its output filename is exactly the
drift that would leave ``ada convert`` exiting 0 with no file where the user asked for one.
"""

from __future__ import annotations

import pathlib

import pytest

from ada.fem.formats.general import FEATypes, get_fem_exports, get_fem_imports
from ada.fem.formats.utils import interpret_fem_format_from_path
from ada_cli.formats import (
    DEFAULT_READ_BY_EXT,
    DEFAULT_WRITE_BY_EXT,
    FEM_READ_FORMATS,
    FEM_WRITE_FORMATS,
    FEM_WRITE_PRIMARY,
    READ_FORMATS,
    SHARED_WRITE_EXT,
    WRITE_FORMATS,
    primary_name,
)
from ada_cli.main import _build_parser

_REPO = pathlib.Path(__file__).parents[2]
_README = _REPO / "README.md"
_DOCS_PAGE = _REPO / "docs" / "documents" / "cli.rst"

# The prose surfaces live in the repo tree only; the wheel/sdist test env ships tests/ and files/
# and nothing else, so skip there rather than fail. Same guard as test_cli_surface_docs.py.
_needs_repo = pytest.mark.skipif(
    not _DOCS_PAGE.is_file(),
    reason=f"no repo tree at {_REPO} (sdist/wheel test env) — docs/ and README.md are not packaged",
)

#: The ``name`` handed to ``to_fem``; the ``FEM_WRITE_PRIMARY`` patterns interpolate it.
_MODEL_NAME = "nm"

#: What each writer leaves in ``<scratch>/<name>/`` besides its primary deck, for a model that has a
#: step (Sesam writes ``sestra.inp`` only then). Mirrored by the notes column in cli.rst, because
#: ``ada convert`` copies every one of these next to the output the user named.
_DOCUMENTED_SIDECARS: dict[str, tuple[str, ...]] = {
    "abaqus": (),
    "calculix": (),
    "sesam": ("sestra.inp",),
    "usfos": (),
    "code_aster": ("{name}.adapy_fem.json", "{name}.comm", "{name}.name_map.json"),
}


def _drift(cli: set[str], library: set[str], cli_label: str, library_label: str) -> str:
    """A failure message that names the formats, not just the two sets."""
    return (
        f"{cli_label} has drifted from {library_label}:\n"
        f"  in {cli_label} but unknown to {library_label}: {sorted(cli - library) or 'none'}\n"
        f"  in {library_label} but missing from {cli_label}: {sorted(library - cli) or 'none'}\n"
        f"Update src/ada_cli/formats.py (and docs/documents/cli.rst + the README table) to match."
    )


# ── the CLI's FEM tables against the library's own maps ───────────────────


def test_fem_write_formats_are_exactly_what_the_library_can_export():
    """Every writer adapy has must be reachable through ``--to``, and no other name offered."""
    library = {fem_type.value for fem_type in get_fem_exports()}
    assert set(FEM_WRITE_FORMATS) == library, _drift(
        set(FEM_WRITE_FORMATS), library, "FEM_WRITE_FORMATS", "get_fem_exports()"
    )


def test_fem_read_formats_are_exactly_what_the_library_can_import():
    """Same in the other direction for ``--from``."""
    library = {fem_type.value for fem_type in get_fem_imports()}
    assert set(FEM_READ_FORMATS) == library, _drift(
        set(FEM_READ_FORMATS), library, "FEM_READ_FORMATS", "get_fem_imports()"
    )


@pytest.mark.parametrize("name", sorted(set(FEM_READ_FORMATS) | set(FEM_WRITE_FORMATS)))
def test_every_fem_format_name_is_a_featypes_value(name: str):
    """The names are passed straight to ``to_fem(fem_format=...)`` / ``from_fem``, which resolve
    them through ``FEATypes.from_str``. A typo here is a runtime failure, not a parse error."""
    values = {fem_type.value for fem_type in FEATypes}
    assert name in values, f"{name!r} is not a FEATypes value; known values are {sorted(values)}"


@pytest.mark.parametrize("name", sorted(FEM_WRITE_FORMATS))
def test_fem_write_formats_are_offered_by_the_write_table(name: str):
    """``FEM_WRITE_FORMATS`` says "this name goes through ``to_fem``"; ``WRITE_FORMATS`` is what
    ``--to`` accepts. A name in the first but not the second is unreachable."""
    assert name in WRITE_FORMATS, f"{name!r} is a FEM write format but --to does not offer it"


@pytest.mark.parametrize("name", sorted(FEM_READ_FORMATS))
def test_fem_read_formats_are_offered_by_the_read_table(name: str):
    assert name in READ_FORMATS, f"{name!r} is a FEM read format but --from does not offer it"


def test_fem_write_primary_covers_exactly_the_fem_write_formats():
    """``_write_fem`` indexes ``FEM_WRITE_PRIMARY[fmt]`` unguarded, so a missing key is a KeyError
    in the middle of a conversion the user has already paid for."""
    assert set(FEM_WRITE_PRIMARY) == set(FEM_WRITE_FORMATS), _drift(
        set(FEM_WRITE_PRIMARY), set(FEM_WRITE_FORMATS), "FEM_WRITE_PRIMARY", "FEM_WRITE_FORMATS"
    )
    for name, pattern in FEM_WRITE_PRIMARY.items():
        # Through the helper, so a pattern that grows a new field fails here rather than at the
        # call site that forgot to supply it.
        rendered = primary_name(name, "nm")
        assert rendered and "{" not in rendered, f"FEM_WRITE_PRIMARY[{name!r}] = {pattern!r} is not a filename pattern"


# ── extension inference against the library's interpretation ─────────────


@pytest.mark.parametrize("ext", sorted(ext for ext, fmt in DEFAULT_READ_BY_EXT.items() if fmt in FEM_READ_FORMATS))
def test_fem_read_extension_defaults_agree_with_interpret_fem_format_from_path(ext: str):
    """``ada.from_fem`` resolves a bare path through ``interpret_fem_format_from_path``. If the CLI
    disagreed, ``ada convert x.fem ...`` and ``ada.from_fem("x.fem")`` would read different formats
    from the same file — the worst kind of drift, because both "work"."""
    library = interpret_fem_format_from_path(f"model.{ext}")
    assert library is not None, f"the library no longer recognises '.{ext}' at all"
    assert DEFAULT_READ_BY_EXT[ext] == library.value, (
        f"'.{ext}' means {DEFAULT_READ_BY_EXT[ext]!r} to the CLI but {library.value!r} to "
        f"ada.fem.formats.utils.interpret_fem_format_from_path"
    )


@pytest.mark.parametrize("ext", sorted(ext for ext, fmt in DEFAULT_WRITE_BY_EXT.items() if fmt in FEM_WRITE_FORMATS))
def test_fem_write_extension_defaults_agree_with_interpret_fem_format_from_path(ext: str):
    """The tie-break for a shared write extension is the same one the library already made, so a
    deck adapy writes to ``out.fem`` is a deck adapy reads back from ``out.fem``."""
    library = interpret_fem_format_from_path(f"model.{ext}")
    assert library is not None, f"the library no longer recognises '.{ext}' at all"
    assert DEFAULT_WRITE_BY_EXT[ext] == library.value, (
        f"writing '.{ext}' means {DEFAULT_WRITE_BY_EXT[ext]!r} to the CLI but reading it means "
        f"{library.value!r} to interpret_fem_format_from_path"
    )


@pytest.mark.parametrize(
    ("label", "formats", "defaults"),
    [("read", READ_FORMATS, DEFAULT_READ_BY_EXT), ("write", WRITE_FORMATS, DEFAULT_WRITE_BY_EXT)],
)
def test_every_extension_has_exactly_one_default_owner(
    label: str, formats: dict[str, tuple[str, ...]], defaults: dict[str, str]
):
    """Inference must be total and unambiguous: every extension any format claims resolves, and it
    resolves to one of the formats that claim it."""
    claimed = {ext for exts in formats.values() for ext in exts}
    missing = sorted(claimed - set(defaults))
    assert not missing, f"{label} extensions with no default owner: {missing}"
    invented = sorted(set(defaults) - claimed)
    assert not invented, f"{label} defaults for extensions no format claims: {invented}"
    for ext, owner in defaults.items():
        claimants = sorted(name for name, exts in formats.items() if ext in exts)
        assert owner in claimants, f"'.{ext}' defaults to {owner!r}, which does not claim it; claimants: {claimants}"


def test_shared_write_extensions_are_exactly_the_contested_ones():
    """``SHARED_WRITE_EXT`` drives the ``--list-formats`` hint and the docs sentence about reaching
    the minority dialect. A new contested extension that is not listed there is a format the user
    is never told how to reach."""
    contested = {
        ext: tuple(sorted(name for name, exts in WRITE_FORMATS.items() if ext in exts))
        for ext in {e for exts in WRITE_FORMATS.values() for e in exts}
    }
    contested = {ext: owners for ext, owners in contested.items() if len(owners) > 1}
    assert set(SHARED_WRITE_EXT) == set(contested), _drift(
        set(SHARED_WRITE_EXT), set(contested), "SHARED_WRITE_EXT", "the WRITE_FORMATS table"
    )
    for ext, owners in SHARED_WRITE_EXT.items():
        assert (
            tuple(sorted(owners)) == contested[ext]
        ), f"SHARED_WRITE_EXT['{ext}'] = {owners} but WRITE_FORMATS says {contested[ext]}"
        others = [o for o in owners if o != DEFAULT_WRITE_BY_EXT[ext]]
        assert others, f"'.{ext}' is listed as shared but every claimant is the default owner"


def test_formats_the_library_cannot_write_are_not_offered():
    """``FEATypes`` carries names with no ``default_pre_processor`` (gmsh, xdmf). They are
    deliberately out of ``--to``; this fails if one gains a writer, which is a prompt to add it."""
    exportable = {fem_type.value for fem_type in get_fem_exports()}
    for fem_type in FEATypes:
        if fem_type.value in exportable:
            continue
        assert (
            fem_type.value not in WRITE_FORMATS
        ), f"--to offers {fem_type.value!r}, but get_fem_exports() has no writer for it"


def test_calculix_is_deliberately_write_only():
    """``interpret_fem_format_from_path`` maps '.frd' to calculix, but ``.frd`` is a *results* file
    and ``get_fem_imports()`` has no Calculix reader. Offering ``--from calculix`` would promise a
    reader that does not exist, so the CLI omits both the name and the extension. Calculix decks are
    read with ``--from abaqus``."""
    readable = {fem_type.value for fem_type in get_fem_imports()}
    assert "calculix" not in readable, "adapy gained a Calculix reader — offer --from calculix"
    assert "calculix" not in READ_FORMATS, "--from offers calculix, but adapy has no Calculix reader"
    assert "frd" not in DEFAULT_READ_BY_EXT, "'.frd' is a results file, not a model — it must not be inferable"
    assert "abaqus" in READ_FORMATS, "Calculix decks are read through the abaqus reader"


# ── the writers themselves ───────────────────────────────────────────────


@pytest.fixture(scope="module")
def fem_write_outcomes(tmp_path_factory) -> dict[str, tuple[pathlib.Path, list[str], BaseException | None]]:
    """Write one small model with every FEM writer, once, and report what landed on disk.

    The model carries a step *and* a boundary condition on purpose, and neither is incidental:

    * the Calculix writer does ``step_str(assembly.fem.steps[0])`` unconditionally and raises
      ``IndexError`` on a model with no step (a sibling change guards this; giving the fixture a
      step means this contract test does not depend on that landing);
    * the Code_Aster step writer raises ``NoBoundaryConditionsApplied`` when a step has no BC.

    The filename a writer chooses does not depend on either, which is all this test is about.

    Each writer's outcome is captured rather than raised, so a broken writer fails only its own
    parametrised case instead of erroring the whole module.
    """
    import ada
    from ada.fem import Bc, FemSet, LoadGravity, StepImplicitStatic

    part = ada.Part("MyPart")
    plate = ada.Plate("pl", [(0, 0), (1, 0), (1, 1), (0, 1)], 0.01)
    part.fem = plate.to_fem_obj(0.5, "shell", use_quads=True)
    assembly = ada.Assembly("ContractModel") / (part / plate)

    fixed = part.fem.add_set(FemSet("bc_nodes", [n for n in part.fem.nodes if n.x < 1e-6], FemSet.TYPES.NSET))
    assembly.fem.add_bc(Bc("Fixed", fixed, [1, 2, 3]))
    step = assembly.fem.add_step(StepImplicitStatic("static", total_time=1, max_incr=1, init_incr=1, nl_geom=False))
    step.add_load(LoadGravity("Gravity"))

    root = tmp_path_factory.mktemp("fem_write_primary")
    outcomes: dict[str, tuple[pathlib.Path, list[str], BaseException | None]] = {}
    for fmt in FEM_WRITE_FORMATS:
        scratch = root / fmt
        scratch.mkdir(parents=True, exist_ok=True)
        error: BaseException | None = None
        try:
            assembly.to_fem(_MODEL_NAME, fmt, scratch_dir=scratch, overwrite=True, write_input_files_only=True)
        except BaseException as exc:  # noqa: BLE001 - reported per format below
            error = exc
        produced = scratch / _MODEL_NAME
        listing = sorted(p.relative_to(produced).as_posix() for p in produced.rglob("*") if p.is_file())
        outcomes[fmt] = (produced, listing, error)
    return outcomes


@pytest.mark.parametrize("fmt", sorted(FEM_WRITE_FORMATS))
def test_primary_pattern_matches_what_each_writer_produces(fmt: str, fem_write_outcomes):
    """``FEM_WRITE_PRIMARY[fmt]`` is the file ``ada convert`` moves to the path the user named. If a
    writer renames its deck, the CLI has nothing to move and the promise "OUT is the file you asked
    for" breaks — so assert against the writer, not against a second copy of the table."""
    produced, listing, error = fem_write_outcomes[fmt]
    if error is not None:
        pytest.fail(f"the {fmt} writer raised {type(error).__name__}: {error}")

    pattern = FEM_WRITE_PRIMARY[fmt]
    primary = produced / primary_name(fmt, _MODEL_NAME)
    assert primary.is_file(), (
        f"FEM_WRITE_PRIMARY[{fmt!r}] = {pattern!r} promises {primary.name!r}, which the {fmt} writer did not "
        f"produce.\n  looked in: {produced}\n  found:     {listing or ['nothing']}\n"
        f"Fix src/ada_cli/formats.py::FEM_WRITE_PRIMARY (and the sidecar notes in docs/documents/cli.rst)."
    )


@pytest.mark.parametrize("fmt", sorted(FEM_WRITE_FORMATS))
def test_writers_leave_only_the_sidecars_the_docs_promise(fmt: str, fem_write_outcomes):
    """``ada convert`` moves every non-primary file next to the output, and the docs page lists what
    to expect per format. A new sidecar is not a bug, but it does land in the user's output
    directory unannounced, so it has to be documented before this passes again."""
    produced, listing, error = fem_write_outcomes[fmt]
    if error is not None:
        pytest.skip(f"the {fmt} writer raised {type(error).__name__} — covered by the primary-file test")

    primary = primary_name(fmt, _MODEL_NAME)
    sidecars = sorted(set(listing) - {primary})
    expected = sorted(s.format(name=_MODEL_NAME) for s in _DOCUMENTED_SIDECARS[fmt])
    assert sidecars == expected, (
        f"the {fmt} writer's sidecars changed: expected {expected or ['none']}, found {sidecars or ['none']}.\n"
        f"Every file here is copied next to the output the user named, so update the per-format notes in "
        f"docs/documents/cli.rst and _DOCUMENTED_SIDECARS together."
    )


# ── the prose surfaces ───────────────────────────────────────────────────


@_needs_repo
@pytest.mark.parametrize("name", sorted(set(READ_FORMATS) | set(WRITE_FORMATS)))
def test_docs_page_names_every_format(name: str):
    """A format the CLI accepts but the reference page never mentions is a format nobody finds."""
    assert name in _DOCS_PAGE.read_text(
        encoding="utf-8"
    ), f"docs/documents/cli.rst never mentions the {name!r} format, which ada convert accepts"


@_needs_repo
@pytest.mark.parametrize(
    "ext",
    sorted({e for exts in READ_FORMATS.values() for e in exts} | {e for exts in WRITE_FORMATS.values() for e in exts}),
)
def test_docs_page_names_every_extension(ext: str):
    """Extensions are how the format is chosen by default, so the page owes the reader all of them."""
    assert f".{ext}" in _DOCS_PAGE.read_text(
        encoding="utf-8"
    ), f"docs/documents/cli.rst never mentions '.{ext}', which ada convert infers a format from"


@_needs_repo
def test_docs_page_documents_every_convert_flag():
    """The page is the full reference for ``ada convert``, and the flags are where the FEM work is
    reachable from (``--to calculix`` is the only route to the Calculix writer). Derived from the
    parser so a new flag cannot be added without a line here."""
    convert = _convert_parser()
    flags = {opt for action in convert._actions for opt in action.option_strings if opt.startswith("--")}
    flags -= {"--help"}
    page = _DOCS_PAGE.read_text(encoding="utf-8")
    undocumented = sorted(flag for flag in flags if f"``{flag}``" not in page)
    assert not undocumented, f"docs/documents/cli.rst does not document these ada convert flags: {undocumented}"


@_needs_repo
@pytest.mark.parametrize("name", sorted(set(READ_FORMATS) | set(WRITE_FORMATS)))
def test_readme_convert_row_names_every_format(name: str):
    """The README summarises rather than enumerates flags, but the format list is the reason to read
    the row at all — and ``calculix``/``usfos`` were the formats the CLI could not reach."""
    row = _readme_convert_row()
    assert name in row, f"the `ada convert` row in README.md does not mention the {name!r} format:\n  {row}"


def _convert_parser():
    parser = _build_parser()
    for action in parser._actions:
        if hasattr(action, "choices") and isinstance(action.choices, dict) and "convert" in action.choices:
            return action.choices["convert"]
    raise AssertionError("ada_cli.main._build_parser() has no 'convert' subcommand")


def _readme_convert_row() -> str:
    rows = [
        line
        for line in _README.read_text(encoding="utf-8").splitlines()
        if line.startswith("|") and "`ada convert`" in line
    ]
    assert len(rows) == 1, f"expected exactly one `ada convert` table row in README.md, found {len(rows)}"
    return rows[0]
