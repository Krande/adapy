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
    FEM_WRITE_PRIMARY,
    READ_FORMATS,
    SHARED_WRITE_EXT,
    WRITE_FORMATS,
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


def _deck_name(out: pathlib.Path, fmt: str) -> str:
    """The ``name`` to hand ``to_fem`` so that its primary file lands on ``out``.

    Normally ``OUT``'s stem. Sesam is the exception: it writes ``<name>T1.FEM`` (``T1`` being
    Sesam's own suffix for an input deck, ``R1`` for results), so a user who names the target
    the way Sesam itself would — ``modelT1.FEM`` — must not silently get a ``modelT1T1.FEM``
    renamed behind their back. Stripping the ``T1`` also keeps ``sestra.inp``'s ``INAM`` /
    ``LNAM`` prefix in agreement with the deck actually on disk.

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
    if fmt == "sesam" and len(name) > 2 and name[-2:].upper() == "T1":
        name = name[:-2]
    return name.replace(".", "_")


def _listing(directory: pathlib.Path) -> str:
    """A listing of ``directory``, for the message when a writer contract turns out broken."""
    if not directory.is_dir():
        return f"  <nothing: {directory} does not exist>"
    found = sorted(str(p.relative_to(directory)) for p in directory.rglob("*"))
    return "\n".join(f"  {f}" for f in found) if found else "  <empty>"


def _write_fem(model, output_file, fmt: str) -> list[pathlib.Path]:
    """Write a FEM deck so that its primary file *is* ``output_file``.

    Returns every path written, primary first. See the module docstring for why this goes
    through a temporary directory next to the destination.
    """
    out = _prepare_out(output_file)
    name = _deck_name(out, fmt)

    tmp = pathlib.Path(tempfile.mkdtemp(prefix=".ada-convert-", dir=out.parent))
    try:
        model.to_fem(
            name,
            fem_format=fmt,
            scratch_dir=tmp,
            overwrite=True,
            write_input_files_only=True,
        )

        produced = tmp / name
        primary = produced / FEM_WRITE_PRIMARY[fmt].format(name=name)
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


def _write(model, output_file, fmt: str) -> list[pathlib.Path]:
    """Write ``model`` as ``fmt`` to ``output_file``. Returns every path written."""
    if fmt in FEM_WRITE_FORMATS:
        return _write_fem(model, output_file, fmt)

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
        model.to_gnx(out)
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

    with conversion_report.collect() as report:
        model = _load(args.input, fmt=in_fmt, split=args.split, limit=args.limit)
        written = _write(model, args.output, out_fmt)

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
