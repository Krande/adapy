"""The conversion engine behind ``ada convert``, the loaders behind ``ada view`` and the
result commands behind ``ada results``.

The parser lives in :mod:`ada_cli.main`; this module is only the implementation it imports
lazily, so nothing here runs during ``ada --help``. The format tables come from
:mod:`ada_cli.formats` — the same objects the parser uses for ``choices=`` — so the CLI's
advertised surface and what this module will actually do cannot drift apart.

**What OUT means.** ``OUT`` is the path of *one file*: the primary deck of the chosen
format, at exactly the path the user typed. On a clean exit that file exists there. Formats
that are genuinely multi-file (Code_Aster's ``.comm`` and JSON sidecars, Sesam's
``sestra.inp``) write the rest *beside* it under the writer's own names, and every path
written is printed to stdout, primary first.

That needs a shim, because ``Assembly.to_fem(name, fmt, scratch_dir=...)`` does not take an
output path at all: it creates ``<scratch_dir>/<name>/`` and names the deck after ``name``.
So :func:`_write_fem` writes into a temporary directory, moves the primary onto ``OUT`` and
the rest next to it, then removes the temporary directory. That directory is created *inside*
``OUT``'s parent so it shares a filesystem with the destination and the move is a rename
rather than a copy — these decks reach hundreds of megabytes.
"""

from __future__ import annotations

import argparse
import os
import pathlib
import re
import shutil
import sys
import tempfile

from ada.config import logger
from ada_cli import CliUsageError
from ada_cli.formats import (
    DEFAULT_READ_BY_EXT,
    DEFAULT_WRITE_BY_EXT,
    FEM_READ_FORMATS,
    FEM_WRITE_FORMATS,
    READ_FORMATS,
    RESULT_FORMATS,
    SHARED_WRITE_EXT,
    WRITE_FORMATS,
    infer_result_format,
    primary_name,
    suffix,
)

# ASCII on purpose: this one is *printed* to a console, and an em dash renders as a replacement
# character under the Windows cp1252 default. Log messages elsewhere can afford the nicer dash.
_CODE_ASTER_EXT_MSG = "code_aster output is the .med mesh; the .comm is written beside it - name the output *.med"

#: Format names that are the same model in two containers, and whose name *is* their extension.
#: ``xml`` is the GeniE concept XML as text; ``gnx`` is that same document zipped with its ACIS
#: body, which is the form GeniE opens directly. Everywhere else here an explicit ``--to`` beats
#: the extension and only warns, but between these two the flag selects no conversion at all -- it
#: can only put one container under the other's name, and either result is unopenable, because
#: GeniE reads a .gnx by unzipping it and an XML parser reads a .xml by parsing it. So the
#: contradiction is refused, for the same reason naming a code_aster ``.comm`` is: the file we
#: would deliver is not the file that was asked for.
_CONTAINER_FORMATS: frozenset[str] = frozenset({"xml", "gnx"})


def _ext_label(path) -> str:
    """How an extension is named in an error message, including when there isn't one."""
    ext = suffix(path)
    return f"'.{ext}'" if ext else f"'{pathlib.Path(path).name}' (no extension)"


def _resolve_read_format(input_file, fmt: str | None) -> str:
    """The input format to use: an explicit ``--from`` wins, otherwise the extension decides."""
    if fmt is not None:
        if fmt not in READ_FORMATS:
            raise CliUsageError(f"unknown input format {fmt!r}; --from must be one of: {', '.join(READ_FORMATS)}")
        return fmt

    inferred = DEFAULT_READ_BY_EXT.get(suffix(input_file))
    if inferred is None:
        raise CliUsageError(
            f"cannot infer the input format from {_ext_label(input_file)}; "
            f"pass --from one of: {', '.join(READ_FORMATS)}"
        )
    return inferred


def _resolve_write_format(output_file, fmt: str | None) -> str:
    """The output format to use.

    An explicit ``--to`` always wins; without one the extension decides, and every extension
    has exactly one default owner. ``.inp`` is both Abaqus and Calculix and ``.fem`` is both
    Sesam and USFOS, so those two resolve to the format ``ada.from_fem`` would also pick and
    say so at INFO — the minority dialect is reached with ``--to``.
    """
    ext = suffix(output_file)

    if fmt is None:
        inferred = DEFAULT_WRITE_BY_EXT.get(ext)
        if inferred is None:
            if ext == "comm":
                raise CliUsageError(_CODE_ASTER_EXT_MSG)
            raise CliUsageError(
                f"cannot infer the output format from {_ext_label(output_file)}; "
                f"pass --to one of: {', '.join(WRITE_FORMATS)}"
            )
        others = [o for o in SHARED_WRITE_EXT.get(ext, ()) if o != inferred]
        if others:
            hint = " or ".join(f"--to {o} for {o.upper()}" for o in others)
            logger.info("output format %r inferred from '.%s' (use %s)", inferred, ext, hint)
        fmt = inferred
    elif fmt not in WRITE_FORMATS:
        raise CliUsageError(f"unknown output format {fmt!r}; --to must be one of: {', '.join(WRITE_FORMATS)}")

    # Code_Aster is the one format whose primary file is not negotiable: the mesh is the .med
    # and the .comm is a sidecar, so naming the .comm asks for an OUT we cannot deliver.
    if fmt == "code_aster" and ext != "med":
        raise CliUsageError(_CODE_ASTER_EXT_MSG)

    # See _CONTAINER_FORMATS: between xml and gnx the extension decides the container and --to has
    # nothing left to choose, so a disagreement is a contradiction rather than an override.
    if fmt in _CONTAINER_FORMATS and ext in _CONTAINER_FORMATS and ext != fmt:
        raise CliUsageError(
            f"--to {fmt} contradicts the output name '.{ext}': .{ext} and .{fmt} are the same "
            f"model in two containers, so --to has nothing to choose between them and a .{ext} "
            f"holding {fmt} would not open. Name the output *.{fmt}, or drop --to."
        )

    usual = WRITE_FORMATS[fmt]
    if ext not in usual:
        logger.warning(
            "writing %s to '%s'; the usual extension is %s",
            fmt,
            pathlib.Path(output_file).name,
            " or ".join(f".{e}" for e in usual),
        )

    return fmt


def _load(input_file, fmt: str | None = None, split: bool = False, limit: int | None = None):
    """Read ``input_file`` into an :class:`~ada.Assembly`.

    ``fmt`` is a resolved ``ada_cli.formats`` read name, or ``None`` to infer it from the
    extension. Shared with ``ada view``, which never resolves a format of its own.
    """
    import ada

    path = pathlib.Path(input_file)
    if not path.is_file():
        raise CliUsageError(f"input file not found: {input_file}")

    fmt = _resolve_read_format(input_file, fmt)

    if fmt == "ifc":
        return ada.from_ifc(path)
    if fmt == "step":
        return ada.from_step(path)
    if fmt == "xml":
        return ada.from_genie_xml(path)
    if fmt == "gnx":
        return ada.from_gnx(path)
    if fmt == "acis":
        return ada.from_acis(path, split=split, limit=limit)
    if fmt in FEM_READ_FORMATS:
        return ada.from_fem(path, fem_format=fmt)

    raise CliUsageError(f"reading {fmt!r} is not implemented; --from must be one of: {', '.join(READ_FORMATS)}")


def _validate_out(output_file) -> pathlib.Path:
    """Check that ``OUT`` names a file and return it resolved. Touches nothing on disk.

    Separate from :func:`_prepare_out` so ``_cmd_convert`` can reject a directory-shaped
    ``OUT`` *before* reading the input, rather than after a multi-minute parse.
    """
    raw = str(output_file)
    out = pathlib.Path(output_file)
    if raw.endswith(("/", "\\")) or out.is_dir():
        raise CliUsageError(f"OUT must name a file, not a directory: {out}")

    out = out.resolve()
    if not out.name or not out.stem:
        raise CliUsageError(f"OUT must name a file, not a directory: {output_file}")
    return out


def _prepare_out(output_file) -> pathlib.Path:
    """Validate ``OUT`` and make sure its parent directory exists.

    ``to_stp`` and ``to_ifc`` do not create their destination's parent, and a path the user
    meant as a directory must be refused rather than silently turned into a file name.
    """
    out = _validate_out(output_file)
    out.parent.mkdir(parents=True, exist_ok=True)
    return out


_SESAM_T_NUMBER = re.compile(r"^(?P<prefix>.*?)[Tt](?P<number>\d+)$")


def _sesam_superelement(out: pathlib.Path) -> int | None:
    """The super element number ``OUT``'s name asks for, or ``None`` if it does not say.

    Sesam names an input interface file ``<prefix>T<n>.FEM``, where ``n`` is the super element
    number that the deck's own IDENT record must also carry — Presel matches the two. So a user
    who types ``myPrefixT10.FEM`` has already said which super element they want, and reading it
    from the name is how the file and the deck are kept from disagreeing.

    ``R<n>`` is results and ``L<n>`` loads; only ``T`` is this format, so only ``T`` is matched.
    """
    match = _SESAM_T_NUMBER.match(out.stem)
    return int(match.group("number")) if match else None


def _deck_name(out: pathlib.Path, fmt: str) -> str:
    """The ``name`` to hand ``to_fem`` so that its primary file lands on ``out``.

    Normally ``OUT``'s stem. Sesam is the exception: it writes ``<name>T<n>.FEM`` (``T`` being
    Sesam's own marker for an input deck, ``R`` for results), so a user who names the target the
    way Sesam itself would — ``modelT10.FEM`` — must not silently get a ``modelT10T10.FEM``
    renamed behind their back. Stripping the ``T<n>`` also keeps ``sestra.inp``'s ``INAM`` /
    ``LNAM`` prefix in agreement with the deck actually on disk.

    A stem that is *only* a T-number (``T100.FEM``, as Presel itself writes) leaves no prefix to
    keep, so the stem stands as the internal name and the writer's intermediate file doubles the
    number. That file is renamed onto ``OUT`` immediately, so the only visible trace would be
    ``INAM`` in a ``sestra.inp``, which is written only for a deck carrying a step.

    A dot inside the stem is replaced. The sesam, calculix and code_aster writers build their
    filenames with ``Path.with_suffix``, which *replaces* whatever it takes to be a suffix, so
    the name ``model.v2`` makes the sesam writer emit ``model.FEM`` instead of
    ``model.v2T1.FEM`` and the deck is not where this function promised it would be.
    ``model.v2.FEM`` and ``beam_0.5m.FEM`` are ordinary names, so the *internal* name is
    sanitised rather than the user's. ``OUT`` is delivered by ``os.replace`` and keeps every
    dot that was typed; the only other trace is Sesam's ``INAM`` prefix in ``sestra.inp``,
    which could not have carried the dotted name either.
    """
    name = out.stem
    if fmt == "sesam":
        match = _SESAM_T_NUMBER.match(name)
        if match and match.group("prefix"):
            name = match.group("prefix")
    return name.replace(".", "_")


def _listing(directory: pathlib.Path) -> str:
    """A listing of ``directory``, for the message when a writer contract turns out broken."""
    if not directory.is_dir():
        return f"  <nothing: {directory} does not exist>"
    found = sorted(str(p.relative_to(directory)) for p in directory.rglob("*"))
    return "\n".join(f"  {f}" for f in found) if found else "  <empty>"


_SESAM_FILE_NUMBER = re.compile(r"^(?P<prefix>.*?)[RrTt](?P<number>\d+)$")


def _input_name_number(input_file) -> int | None:
    """The number in an input named the Sesam way: ``R<n>.SIN`` (results) or ``<prefix>T<n>.FEM``."""
    match = _SESAM_FILE_NUMBER.match(pathlib.Path(input_file).stem)
    return int(match.group("number")) if match else None


def _input_seltyp(model) -> int | None:
    """The super element number the input model says it is (a Sesam deck's IDENT SELTYP), if any."""
    for part in model.get_all_parts_in_assembly(include_self=True):
        fem = getattr(part, "fem", None)
        number = fem.metadata.get("sesam_seltyp") if fem is not None else None
        if number is not None:
            return int(number)
    return None


def _resolve_superelement(
    out: pathlib.Path, explicit: int | None, from_input: int | None = None, input_file=None
) -> int:
    """The Sesam super element number to write, and say where it came from.

    In order: an explicit ``--superelement``; ``OUT``'s own ``T<n>``; what the input says
    (``from_input``: the superelement a SIN is read from, a deck's IDENT SELTYP); the number in the
    input's file name (``R<n>.SIN``, ``<prefix>T<n>.FEM``); and 1. The fallback to 1 is *said*, not
    assumed, because a silently defaulted 1 in a file the user named ``…T10.FEM`` is exactly the
    mismatch this resolution exists to prevent: Presel would read super element 1 from a deck the
    assembly expects to be 10.

    Called before the input is read with only ``explicit`` to refuse a contradiction early, and
    again after reading with what the input says.
    """
    if explicit is not None:
        if explicit < 1:
            raise CliUsageError(f"--superelement must be 1 or greater, got {explicit}")
        from_name = _sesam_superelement(out)
        if from_name is not None and from_name != explicit:
            # Refused rather than warned about: honouring either number leaves a file whose name
            # says one super element and whose IDENT says another, which is the mismatch this
            # resolution exists to prevent. The user has given two answers to one question.
            raise CliUsageError(
                f"--superelement {explicit} contradicts the T-number in '{out.name}', which names "
                f"super element {from_name}. Sesam expects a deck's IDENT SELTYP and its "
                f"<prefix>T<n>.FEM name to agree, so pick one: drop the flag, or name the output "
                f"'{out.stem[: -len(str(from_name)) - 1]}T{explicit}{out.suffix}'."
            )
        return explicit

    from_name = _sesam_superelement(out)
    if from_name is not None:
        logger.info("super element %s, from the T-number in '%s'", from_name, out.name)
        return from_name

    if from_input is not None:
        logger.info("super element %s, from the input's own super element number", from_input)
        return from_input

    from_file = _input_name_number(input_file) if input_file is not None else None
    if from_file is not None:
        name = pathlib.Path(input_file).name
        logger.info("super element %s, from the number in the input's name '%s'", from_file, name)
        return from_file

    logger.info(
        "'%s' names no super element number, so writing super element 1 "
        "(IDENT SELTYP 1); name the output '<prefix>T<n>.FEM' or pass --superelement to choose",
        out.name,
    )
    return 1


def _write_fem(model, output_file, fmt: str, superelement: int | None = None) -> list[pathlib.Path]:
    """Write a FEM deck so that its primary file *is* ``output_file``.

    Returns every path written, primary first. See the module docstring for why this goes
    through a temporary directory next to the destination.
    """
    out = _prepare_out(output_file)
    name = _deck_name(out, fmt)
    seltyp = superelement if superelement is not None else 1

    tmp = pathlib.Path(tempfile.mkdtemp(prefix=".ada-convert-", dir=out.parent))
    try:
        # ``metadata`` is passed only for sesam: it is the one writer with something to say here,
        # and a writer that takes no metadata should not be handed the keyword at all.
        extra = {"metadata": {"sesam_superelement": seltyp}} if fmt == "sesam" else {}
        model.to_fem(
            name,
            fem_format=fmt,
            scratch_dir=tmp,
            overwrite=True,
            write_input_files_only=True,
            **extra,
        )

        produced = tmp / name
        primary = produced / primary_name(fmt, name, seltyp)
        if not primary.is_file():
            raise RuntimeError(
                f"the {fmt} writer did not produce {primary.name!r}, which "
                f"ada_cli.formats.FEM_WRITE_PRIMARY says it should. It wrote:\n{_listing(produced)}"
            )

        os.replace(primary, out)
        written = [out]

        # Sidecars: everything else the writer left behind, under its own name, beside OUT.
        for extra in sorted(produced.rglob("*")):
            if not extra.is_file():
                continue
            dest = out.parent / extra.name
            if dest == out:
                # Would overwrite the deck we just delivered; the deck wins.
                logger.warning("skipping sidecar %s: it would overwrite the output file", extra.name)
                continue
            if dest.exists():
                # Sidecars keep the writer's own names (sestra.inp, <name>.comm), so a second
                # conversion into a directory that already holds one silently replaces it.
                # Say so: the user named OUT, not this.
                logger.warning("overwriting existing %s with the %s writer's sidecar", dest, fmt)
            os.replace(extra, dest)
            written.append(dest)

        return written
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        if tmp.exists():
            # ignore_errors swallows the reason (an open handle, a scanner holding a
            # file). Say it is there rather than let the docs' "nothing else is left
            # behind" quietly become false.
            logger.warning("could not remove the temporary directory %s; it can be deleted", tmp)


def _write(
    model, output_file, fmt: str, superelement: int | None = None, binary_acis: bool = False
) -> list[pathlib.Path]:
    """Write ``model`` as ``fmt`` to ``output_file``. Returns every path written."""
    if fmt in FEM_WRITE_FORMATS:
        return _write_fem(model, output_file, fmt, superelement=superelement)

    out = _prepare_out(output_file)
    if fmt == "ifc":
        model.to_ifc(out)
    elif fmt == "step":
        model.to_stp(out)
    elif fmt == "gltf":
        model.to_gltf(out)
    elif fmt == "xml":
        model.to_genie_xml(out)
    elif fmt == "gnx":
        model.to_gnx(out, binary_acis=binary_acis)
    else:
        raise CliUsageError(f"writing {fmt!r} is not implemented; --to must be one of: {', '.join(WRITE_FORMATS)}")

    return [out]


def _report_path(out: pathlib.Path) -> pathlib.Path:
    """Where the conversion report goes: ``<OUT stem>_conversion_report.json``, beside ``OUT``."""
    return out.with_name(f"{out.stem}_conversion_report.json")


def _numbered_output(out: pathlib.Path, number: int) -> pathlib.Path:
    """``<dir>/<stem>T<number><suffix>`` for ``out`` = ``<dir>/<stem><suffix>``; a trailing ``T<n>`` of the
    stem is dropped first, so ``outT1.FEM`` gives ``outT10.FEM`` rather than ``outT1T10.FEM``."""
    stem = re.sub(r"[Tt]\d+$", "", out.stem)
    return out.with_name(f"{stem}T{number}{out.suffix}")


def _convert_sin(args, out: pathlib.Path, out_fmt: str, explicit, specs, merge: bool, binary_acis: bool):
    """Convert a Sesam .SIN, which may hold an assembly of superelements, without dropping any silently.

    * ``merge``: every first-level superelement with a mesh, placed and merged, as ONE file at OUT
      carrying the top level's number.
    * ``specs`` (``--super-element``): just those, the one as OUT itself, several as numbered files.
    * neither: an assembly gives one numbered file per first-level superelement with a mesh
      (``<stem>T<n><suffix>``, each with its own number); a SIN holding one superelement is
      written as OUT, as always.
    """
    from ada.fem.formats import conversion_report
    from ada.fem.formats.sesam.read import sin_assembly as sa

    path = pathlib.Path(args.input)
    if not path.is_file():
        raise CliUsageError(f"input file not found: {args.input}")
    sesam = out_fmt == "sesam"

    def write_one(model, input_number: int | None) -> list[pathlib.Path]:
        seltyp = _resolve_superelement(out, explicit, input_number, args.input) if sesam else None
        return _write(model, args.output, out_fmt, superelement=seltyp, binary_acis=binary_acis)

    try:
        layout = sa.sin_layout(path)
        for row in layout.deeper:
            conversion_report.current().omitted(
                "sin reader",
                "Superelement",
                row.label,
                "a superelement below the first level; only first-level superelements are read",
            )

        if merge:
            if not layout.is_assembly:
                raise CliUsageError(
                    f"--merge applies to a SIN holding more than one first-level superelement with a mesh; "
                    f"this one holds {len(layout.meshes)}"
                )
            model = sa.read_merged(path, layout)
            return write_one(model, layout.top.seltyp)

        if specs:
            chosen = sa.resolve_specs(path, layout, specs)
        else:
            chosen = list(layout.meshes) if layout.is_assembly else layout.meshes[:1]

        if len(chosen) <= 1:
            row = chosen[0] if chosen else None
            model = sa.read_superelement(path, row if (layout.is_assembly or specs) else None)
            return write_one(model, row.seltyp if row is not None else _input_seltyp(model))

        if out_fmt not in FEM_WRITE_FORMATS:
            raise CliUsageError(
                f"{path.name} holds {len(chosen)} superelements ({', '.join(r.label for r in chosen)}), which "
                f"cannot be written one per file as {out_fmt}. Use --merge for one model, or --super-element "
                "to choose one."
            )
        if explicit is not None:
            raise CliUsageError(
                f"--superelement {explicit} numbers one file, but {len(chosen)} superelements are being written, "
                "each with its own number. Use --super-element to choose one, or --merge for one file."
            )
        targets = {row.label: _numbered_output(out, row.seltyp) for row in chosen}
        if len(set(targets.values())) != len(targets):
            raise CliUsageError(
                "two superelements are instances of one type and would be written to the same file name; "
                "choose with --super-element or combine them with --merge"
            )
        written: list[pathlib.Path] = []
        for row in chosen:
            model = sa.read_superelement(path, row)
            written += _write(
                model, targets[row.label], out_fmt, superelement=row.seltyp if sesam else None, binary_acis=binary_acis
            )
            del model
        logger.info("wrote %d superelements: %s", len(chosen), ", ".join(r.label for r in chosen))
        return written
    except sa.SinAssemblyError as exc:
        raise CliUsageError(str(exc)) from None


def _cmd_convert(args: argparse.Namespace) -> int:
    """Convert IN to OUT, and say what did not survive the trip.

    Returns 0, or 3 under ``--strict`` when something was omitted or the input looks wrong. 2 is
    argparse's usage exit and stays reserved for :class:`CliUsageError`. Approximations alone do
    not fail ``--strict``: a tie resolved to the nearest node is always one.

    A JSON report is written beside OUT **when anything needs a human's decision**, and its
    path is printed with the others. A clean conversion leaves OUT alone: a file that appears on
    every single run is furniture nobody reads, while one that appears only when something needs
    attention is a signal. ``--strict`` and the exit code, not the file's presence, are how a
    script asks whether the conversion was complete.

    Notes alone do not bring the file into being: a note is context for the findings around it,
    not news on its own. When the file *is* written, every note is in it.

    A short summary always goes to **stderr** — after the paths, which go to stdout — so that
    ``ada convert in out > written.txt`` still yields nothing but paths, and so the summary
    survives ``--log-file`` raising the console log handler to WARNING.
    """
    from ada.fem.formats import conversion_report

    # Every usage error is raised before the input is touched: one must not cost the
    # multi-minute parse of a large deck first.
    in_fmt = _resolve_read_format(args.input, getattr(args, "from_format", None))
    out_fmt = _resolve_write_format(args.output, getattr(args, "to_format", None))
    out = _validate_out(args.output)
    explicit = getattr(args, "superelement", None)
    # Checked here, not in _write_fem, so a contradictory --superelement is refused before the
    # input is read rather than after a multi-minute parse. What the input itself says about the
    # number can only be resolved after reading it.
    if out_fmt == "sesam" and explicit is not None:
        _resolve_superelement(out, explicit)
    sin_input = in_fmt == "sesam" and suffix(args.input) == "sin"
    merge = getattr(args, "merge", False)
    specs = getattr(args, "super_element", None)
    if merge and not sin_input:
        raise CliUsageError("--merge applies to a Sesam .SIN assembly only")
    if specs and not sin_input:
        raise CliUsageError("--super-element applies to a Sesam .SIN only")
    if merge and specs:
        raise CliUsageError(
            "--merge writes every first-level superelement as one file; it cannot be combined with --super-element"
        )
    binary_acis = getattr(args, "binary_acis", False)
    if binary_acis and out_fmt != "gnx":
        # Only a GeniE workspace stores its ACIS body as a separate member; anywhere else the flag
        # would be silently meaningless, so it is refused before the input is read.
        raise CliUsageError(f"--binary-acis applies to gnx output only, not {out_fmt}")

    with conversion_report.collect() as report:
        if sin_input:
            written = _convert_sin(args, out, out_fmt, explicit, specs, merge, binary_acis)
        else:
            model = _load(args.input, fmt=in_fmt, split=args.split, limit=args.limit)
            seltyp = (
                _resolve_superelement(out, explicit, _input_seltyp(model), args.input) if out_fmt == "sesam" else None
            )
            written = _write(model, args.output, out_fmt, superelement=seltyp, binary_acis=binary_acis)

    for path in written:
        print(path)

    if report.needs_a_decision:
        print(
            report.write_json(
                _report_path(out),
                input=str(pathlib.Path(args.input).resolve()),
                output=str(out),
                from_format=in_fmt,
                to_format=out_fmt,
            )
        )

    # stderr, not the log: the summary is the one thing the user should not have to open a file
    # to read, and it must not land in a stdout someone is capturing for paths.
    print(report.summary(), file=sys.stderr)

    if getattr(args, "strict", False) and (report.has_omissions or report.has_suspects):
        return 3
    return 0


def _super_element_arg(specs: list[str] | None):
    """``--super-element`` as ``ada.from_fem_res`` takes it: one spec, a list of them, or ``None``."""
    if not specs:
        return None
    return specs[0] if len(specs) == 1 else list(specs)


def _load_results(input_file, super_element: list[str] | None = None):
    """Read the result file ``input_file`` into an :class:`~ada.fem.results.common.FEAResult`."""
    import ada

    path, fmt = _result_file(input_file, super_element)
    return ada.from_fem_res(path, fem_format=fmt, super_element=_super_element_arg(super_element))


def _open_result_chunks(input_file, super_element: list[str] | None = None):
    """``input_file`` as a stream of field chunks when its reader has one (a Sesam .SIN), else None."""
    path, _ = _result_file(input_file, super_element)
    if suffix(path) != "sin":
        return None
    from ada.fem.formats.sesam.results.sin_field_stream import SinFieldStream

    return SinFieldStream.open(path, super_element=_super_element_arg(super_element))


def _result_file(input_file, super_element: list[str] | None) -> tuple[pathlib.Path, str]:
    """``input_file`` and its result format, or :class:`CliUsageError`."""
    path = pathlib.Path(input_file)
    if not path.is_file():
        raise CliUsageError(f"input file not found: {input_file}")

    fmt = infer_result_format(path)
    if fmt is None:
        known = ", ".join("." + e for exts in RESULT_FORMATS.values() for e in exts)
        raise CliUsageError(f"{_ext_label(path)} is not a result file; result files are {known}")
    if super_element and suffix(path) != "sin":
        raise CliUsageError("--super-element applies to a Sesam .SIN only")
    return path, fmt


def _cmd_view(args: argparse.Namespace) -> None:
    from_format = getattr(args, "from_format", None)
    super_element = getattr(args, "super_element", None)

    # A result file shows its results, unless --from asks for it to be read as a model.
    if from_format is None and infer_result_format(args.input) is not None:
        if args.renderer == "trimesh":
            raise CliUsageError("results are shown with --renderer react or pygfx, not trimesh")
        result = _load_results(args.input, super_element)
        if getattr(args, "stats", False):
            print(result.stats(), flush=True)
        result.show(renderer=args.renderer, host=args.host, port=args.ws_port)
        return

    if super_element:
        raise CliUsageError("--super-element applies to a Sesam .SIN result file only")
    if getattr(args, "stats", False):
        raise CliUsageError("--stats applies to result files only")

    model = _load(
        args.input,
        fmt=from_format,
        split=args.split,
        limit=args.limit,
    )
    model.show(renderer=args.renderer, host=args.host, ws_port=args.ws_port)


def _cmd_results_stats(args: argparse.Namespace) -> int:
    result = _load_results(args.input, args.super_element)
    print(repr(result))
    print(result.stats())
    return 0


def _same_file(a, b) -> bool:
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


def _compare(args: argparse.Namespace):
    """The comparison ``ada results compare`` prints. Files that can be read a chunk at a time
    are, so neither result is ever held whole; a file given twice is read once."""
    from ada.fem.results.compare import compare_result_streams

    same = _same_file(args.base, args.other)
    base = _open_result_chunks(args.base, args.super_element)
    other = None if same or base is None else _open_result_chunks(args.other, args.super_element)
    if base is not None and (same or other is not None):
        try:
            return compare_result_streams(
                base, other, fields=args.fields, check_mesh=args.check_mesh, keep_difference=args.show
            )
        finally:
            base.close()
            if other is not None:
                other.close()
    if base is not None:
        base.close()

    full_base = _load_results(args.base, args.super_element)
    full_other = full_base if same else _load_results(args.other, args.super_element)
    return full_base.compare(full_other, fields=args.fields, check_mesh=args.check_mesh)


def _cmd_results_compare(args: argparse.Namespace) -> int:
    from ada.fem.results.compare import DEFAULT_RTOL

    try:
        comparison = _compare(args)
    except ValueError as exc:
        # The meshes differ: a usage mistake (the wrong pair of files), not a failure of the work.
        if "the meshes differ" not in str(exc):
            raise
        message = str(exc).replace("pass check_mesh=False", "pass --no-check-mesh")
        raise CliUsageError(message) from None

    rtol = DEFAULT_RTOL if args.rtol is None else args.rtol
    print(repr(comparison))
    print(comparison.stats(rtol=rtol, max_rows=args.max_rows or None), flush=True)
    if args.show:
        comparison.show(renderer=args.renderer, host=args.host, port=args.ws_port)
    return 0
