"""The conversion engine behind ``ada convert`` (and the loader behind ``ada view``).

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
    SHARED_WRITE_EXT,
    WRITE_FORMATS,
    primary_name,
    suffix,
)

# ASCII on purpose: this one is *printed* to a console, and an em dash renders as a replacement
# character under the Windows cp1252 default. Log messages elsewhere can afford the nicer dash.
_CODE_ASTER_EXT_MSG = "code_aster output is the .med mesh; the .comm is written beside it - name the output *.med"


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


def _resolve_superelement(out: pathlib.Path, explicit: int | None) -> int:
    """The Sesam super element number to write, and say where it came from.

    An explicit ``--superelement`` wins; otherwise ``OUT``'s own ``T<n>`` says it. With neither,
    it is 1 — and that is *said*, not assumed, because a silently defaulted 1 in a file the user
    named ``…T10.FEM`` is exactly the mismatch this resolution exists to prevent: Presel would
    read super element 1 from a deck the assembly expects to be 10.
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


def _write(model, output_file, fmt: str, superelement: int | None = None) -> list[pathlib.Path]:
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
    else:
        raise CliUsageError(f"writing {fmt!r} is not implemented; --to must be one of: {', '.join(WRITE_FORMATS)}")

    return [out]


def _report_path(out: pathlib.Path) -> pathlib.Path:
    """Where the conversion report goes: ``<OUT stem>_conversion_report.json``, beside ``OUT``."""
    return out.with_name(f"{out.stem}_conversion_report.json")


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
    # Resolved here, not in _write_fem, so a contradictory --superelement is refused before the
    # input is read rather than after a multi-minute parse.
    seltyp = _resolve_superelement(out, getattr(args, "superelement", None)) if out_fmt == "sesam" else None

    with conversion_report.collect() as report:
        model = _load(args.input, fmt=in_fmt, split=args.split, limit=args.limit)
        written = _write(model, args.output, out_fmt, superelement=seltyp)

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


def _cmd_view(args: argparse.Namespace) -> None:
    model = _load(
        args.input,
        fmt=getattr(args, "from_format", None),
        split=args.split,
        limit=args.limit,
    )
    model.show(renderer=args.renderer, host=args.host, ws_port=args.ws_port)
