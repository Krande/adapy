"""Single ``ada`` console entry point.

Lives in its own top-level package (``ada_cli``) so that running
``ada --help`` does not trigger ``ada/__init__.py``, which pulls in the
full CAD/FEM surface and adds noticeable startup latency. Each
subcommand imports its implementation lazily, so an invocation only
pays for what it uses.

Subcommand layout:

    ada convert IN OUT [--to F] [--from F]   local format conversion
    ada view IN [--renderer ...]      local web viewer
    ada build run|upload|run-and-upload
                                      build artefacts and push to viewer
    ada files list|download|upload|delete   list / transfer / remove blobs in a scope
    ada audit runs|run|log|perf|profile|fetch|logfile|repro|wasm-sweep|parity
                                      query / re-run viewer audit conversions
    ada serve api|worker              run the REST API / worker process

This listing is the CLI's own claim about its surface, so it is checked against
the parser in tests/core/test_cli_surface_docs.py — as are the README table and
the docs page (docs/documents/cli.rst).

Exit codes: 0 success; 2 for anything wrong with the invocation — an argparse
error, a ``CliUsageError`` raised by an implementation, or a bare command with
nothing to act on; 1 (via an uncaught exception) when the work itself failed.
A bare ``ada`` or ``ada <group>`` prints that parser's *full* help rather than a
one-line usage, but it prints it to stderr and still exits 2, because a wrong
invocation must not look like success to a script.
"""

from __future__ import annotations

import argparse
import sys

from ada_cli.formats import (
    DEFAULT_WRITE_BY_EXT,
    READ_FORMATS,
    SHARED_WRITE_EXT,
    WRITE_FORMATS,
    format_table_text,
)


def _cmd_convert(args: argparse.Namespace) -> int:
    from ada.api.cli import _cmd_convert as impl

    impl(args)
    return 0


def _cmd_view(args: argparse.Namespace) -> int:
    from ada.api.cli import _cmd_view as impl

    impl(args)
    return 0


def _cmd_build_run(args: argparse.Namespace) -> int:
    from ada.build.cli import cmd_run

    return cmd_run(args)


def _cmd_build_upload(args: argparse.Namespace) -> int:
    from ada.build.cli import cmd_upload

    return cmd_upload(args)


def _cmd_build_run_and_upload(args: argparse.Namespace) -> int:
    from ada.build.cli import cmd_run_and_upload

    return cmd_run_and_upload(args)


def _cmd_files_list(args: argparse.Namespace) -> int:
    from ada_cli.files import cmd_list

    return cmd_list(args)


def _cmd_files_upload(args: argparse.Namespace) -> int:
    from ada_cli.files import cmd_upload

    return cmd_upload(args)


def _cmd_files_download(args: argparse.Namespace) -> int:
    from ada_cli.files import cmd_download

    return cmd_download(args)


def _cmd_files_delete(args: argparse.Namespace) -> int:
    from ada_cli.files import cmd_delete

    return cmd_delete(args)


def _cmd_serve_api(_args: argparse.Namespace) -> int:
    from ada.comms.rest.__main__ import run

    run()
    return 0


def _cmd_serve_worker(_args: argparse.Namespace) -> int:
    from ada.comms.rest.worker import run

    run()
    return 0


class _ListFormatsAction(argparse.Action):
    """``--list-formats``: print the format tables, then exit 0.

    Built like ``--version`` rather than as a flag ``_cmd_convert`` would inspect, for one
    reason: ``convert`` has two *required* positionals, so a plain flag could never be reached
    without the user also naming an input and an output they do not have yet. ``nargs=0`` plus
    ``parser.exit`` short-circuits the parse instead.
    """

    def __init__(self, option_strings, dest=argparse.SUPPRESS, default=argparse.SUPPRESS, help=None):
        super().__init__(option_strings=option_strings, dest=dest, default=default, nargs=0, help=help)

    def __call__(self, parser, namespace, values, option_string=None):
        # stdout, unlike parser.exit()'s own message argument: this is the answer that was asked
        # for, the exit is 0, and it is meant to be piped.
        sys.stdout.write(format_table_text())
        parser.exit(0)


def _format_spec(formats: dict[str, tuple[str, ...]]) -> str:
    """``name (.ext/.ext)`` for each format, in table order."""
    return ", ".join(f"{name} ({'/'.join('.' + e for e in exts)})" for name, exts in formats.items())


def _shared_ext_sentence() -> str:
    """How each extension that more than one FEM format claims is resolved without a flag."""
    parts = []
    for ext, owners in SHARED_WRITE_EXT.items():
        default = DEFAULT_WRITE_BY_EXT[ext]
        others = " or --to ".join(o for o in owners if o != default)
        parts.append(f".{ext} means {default} unless you pass --to {others}")
    return "; ".join(parts)


def _convert_description() -> str:
    """The ``ada convert --help`` blurb, generated from the format tables so it cannot drift."""
    return (
        "Convert an input model to another format. "
        f"Reads: {_format_spec(READ_FORMATS)}. "
        f"Writes: {_format_spec(WRITE_FORMATS)}. "
        "The extension decides the format unless --from/--to overrides it. Two extensions are "
        f"claimed by more than one FEM format, and each has one default owner: {_shared_ext_sentence()}. "
        "OUT always names one file, the chosen format's primary deck, and that exact path is what "
        "you get; extra files the format needs are written beside it under the writer's own "
        "names, and every path written is printed. See --list-formats for the tables laid out."
    )


def _add_log_file(p: argparse.ArgumentParser) -> None:
    """``--log-file``, on every subcommand that initialises ``ada`` and so produces log records.

    Per-subcommand rather than next to the global ``--log-level``, because argparse only accepts a
    parser-level option *before* the subcommand, and ``ada convert in out --log-file x`` is how it
    gets typed.
    """
    p.add_argument(
        "--log-file",
        dest="log_file",
        default=None,
        metavar="PATH",
        help=(
            "Write adapy's log records to this file (truncating it) and leave only warnings and "
            "errors on the console. Without it, everything at --log-level goes to stderr."
        ),
    )


def _configure_ada_logging(level: str, log_file: str | None) -> None:
    """Apply ``--log-level``/``--log-file`` to the ``ada`` logger.

    With a log file, the console handler is raised to WARNING: the point of asking for a file is
    that the INFO stream is too big to read on a terminal (a 463k-element deck can emit hundreds of
    thousands of lines), while a warning is still something you want to see as it happens.
    """
    import logging
    import pathlib

    import ada

    ada.logger.setLevel(level)
    ada.logger.propagate = False

    if log_file is None:
        return

    path = pathlib.Path(log_file).expanduser()
    if path.parent != pathlib.Path("."):
        path.parent.mkdir(parents=True, exist_ok=True)

    handler = logging.FileHandler(path, mode="w", encoding="utf-8")
    handler.setFormatter(logging.Formatter("[%(asctime)s: %(levelname)s/%(name)s] | %(message)s"))
    ada.logger.addHandler(handler)

    for existing in ada.logger.handlers:
        # FileHandler subclasses StreamHandler, so exclude it (and the one just added) explicitly.
        if isinstance(existing, logging.StreamHandler) and not isinstance(existing, logging.FileHandler):
            existing.setLevel(logging.WARNING)


def _add_convert(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "convert",
        help="Convert between supported CAD/FEM formats (local).",
        description=_convert_description(),
    )
    p.add_argument("input", help="Input file path.")
    p.add_argument(
        "output",
        help=(
            "Output file path. This exact path is the primary file written; any sidecars the "
            "format needs land beside it."
        ),
    )
    p.add_argument(
        "-f",
        "--from",
        dest="from_format",
        default=None,
        choices=tuple(READ_FORMATS),
        metavar="FORMAT",
        help=(
            "Read the input as this format instead of inferring it from the extension. "
            f"One of: {', '.join(READ_FORMATS)}."
        ),
    )
    p.add_argument(
        "-t",
        "--to",
        dest="to_format",
        default=None,
        choices=tuple(WRITE_FORMATS),
        metavar="FORMAT",
        help=(
            "Write this format instead of inferring it from the output extension. "
            f"One of: {', '.join(WRITE_FORMATS)}."
        ),
    )
    p.add_argument(
        "--list-formats",
        action=_ListFormatsAction,
        help="Print the read/write format tables and exit.",
    )
    p.add_argument("--split", action="store_true", help="Split ACIS/SAT bodies into individual faces.")
    p.add_argument("--limit", type=int, default=None, help="Limit number of geometries (debugging).")
    _add_log_file(p)
    p.set_defaults(func=_cmd_convert, needs_ada_logging=True)


def _add_view(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "view",
        help="Open the built-in web viewer on the given file (local).",
    )
    p.add_argument("input", help="Input file path.")
    p.add_argument(
        "-f",
        "--from",
        dest="from_format",
        choices=tuple(READ_FORMATS),
        metavar="FORMAT",
        help="Read the input as this format instead of inferring it from the extension.",
    )
    p.add_argument("--renderer", default="react", choices=["react", "pygfx", "trimesh"])
    p.add_argument("--host", default="localhost")
    p.add_argument("--ws-port", type=int, default=8765)
    p.add_argument("--split", action="store_true")
    p.add_argument("--limit", type=int, default=None)
    _add_log_file(p)
    p.set_defaults(func=_cmd_view, needs_ada_logging=True)


def _add_build(sub: argparse._SubParsersAction) -> None:
    build = sub.add_parser(
        "build",
        help="Run entrypoints from ada_config.toml and push artefacts to the viewer.",
    )
    build_sub = build.add_subparsers(dest="build_command", required=True)

    def _add_common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--config", default="ada_config.toml", help="Path to ada_config.toml.")
        p.add_argument("--entrypoint", default=None, help="Run only the named entrypoint.")
        p.add_argument("--output-dir", default=".ada-build", help="Where to stage produced artefacts.")

    run = build_sub.add_parser("run", help="Run entrypoints declared in ada_config.toml.")
    _add_common(run)
    run.set_defaults(func=_cmd_build_run)

    upload = build_sub.add_parser(
        "upload",
        help="Upload artefacts under the output dir (needs ADAPY_VIEWER_URL + ADAPY_VIEWER_TOKEN).",
    )
    _add_common(upload)
    upload.set_defaults(func=_cmd_build_upload)

    run_and_upload = build_sub.add_parser("run-and-upload", help="Chain `run` then `upload` (CI default).")
    _add_common(run_and_upload)
    run_and_upload.set_defaults(func=_cmd_build_run_and_upload)


def _add_files(sub: argparse._SubParsersAction) -> None:
    files = sub.add_parser(
        "files",
        help="List, download, or upload blobs in a scope (needs ADAPY_VIEWER_URL + ADAPY_VIEWER_TOKEN).",
    )
    files_sub = files.add_subparsers(dest="files_command", required=True)

    def _add_remote_opts(p: argparse.ArgumentParser) -> None:
        p.add_argument("--url", default=None, help="Viewer base URL (default: $ADAPY_VIEWER_URL).")
        p.add_argument("--token", default=None, help="Bearer token (default: $ADAPY_VIEWER_TOKEN).")
        p.add_argument(
            "--scope",
            default=None,
            help="Scope, e.g. 'project:my-slug' or 'user:me' (default: $ADAPY_VIEWER_SCOPE).",
        )

    ls = files_sub.add_parser("list", help="List files in the scope.")
    _add_remote_opts(ls)
    ls.add_argument("--prefix", default=None, help="Only list keys starting with this prefix.")
    ls.add_argument("-l", "--long", action="store_true", help="Show file sizes alongside keys.")
    ls.set_defaults(func=_cmd_files_list)

    dl = files_sub.add_parser(
        "download",
        help="Download a blob. Goes S3-direct via a presigned URL when the backend supports it.",
    )
    _add_remote_opts(dl)
    dl.add_argument("key", help="Blob key, e.g. versions/main/abc1234/model.glb.")
    dl.add_argument("dest", nargs="?", default=None, help="Destination path (default: basename in CWD).")
    dl.add_argument(
        "--via-api",
        action="store_true",
        help="Force the API-tunneled GET path (skip presigned URL).",
    )
    dl.set_defaults(func=_cmd_files_download)

    up = files_sub.add_parser(
        "upload",
        help="Upload a file. Goes S3-direct via a presigned URL when the backend supports it.",
    )
    _add_remote_opts(up)
    up.add_argument("src", help="Local file to upload.")
    up.add_argument("key", nargs="?", default=None, help="Destination blob key (default: basename of src).")
    up.add_argument(
        "--via-api",
        action="store_true",
        help="Force the API-tunneled PUT path (skip presigned URL; subject to the direct-upload size cap).",
    )
    up.set_defaults(func=_cmd_files_upload)

    rm = files_sub.add_parser("delete", help="Delete one or more blobs in the scope.")
    _add_remote_opts(rm)
    rm.add_argument("keys", nargs="*", help="Blob key(s) to delete, e.g. debug/old.glb.")
    rm.add_argument("--prefix", default=None, help="Also delete every key starting with this prefix.")
    rm.add_argument("-y", "--yes", action="store_true", help="Skip the confirmation prompt.")
    rm.set_defaults(func=_cmd_files_delete)


def _add_audit(sub: argparse._SubParsersAction) -> None:
    from ada_cli.audit import add_parser

    add_parser(sub)


def _add_serve(sub: argparse._SubParsersAction) -> None:
    serve = sub.add_parser("serve", help="Run a long-lived server process.")
    serve_sub = serve.add_subparsers(dest="serve_command", required=True)

    api = serve_sub.add_parser("api", help="Run the REST API (uvicorn).")
    api.set_defaults(func=_cmd_serve_api)

    worker = serve_sub.add_parser("worker", help="Run the conversion worker (NATS JetStream consumer).")
    worker.set_defaults(func=_cmd_serve_worker)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ada",
        description="adapy CLI — convert, view, build, and serve.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        help="Logging level for commands that initialise the ada package (default INFO).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    _add_convert(sub)
    _add_view(sub)
    _add_build(sub)
    _add_files(sub)
    _add_audit(sub)
    _add_serve(sub)

    return parser


def _subparsers_action(parser: argparse.ArgumentParser) -> argparse._SubParsersAction | None:
    """The parser's subcommand action, if it has one."""
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action
    return None


def _bare_invocation_parser(parser: argparse.ArgumentParser, argv: list[str]) -> argparse.ArgumentParser | None:
    """The parser whose full help answers a bare ``ada ...``, or ``None`` if this is not one.

    ``ada`` and ``ada convert`` are invocations that named a command but gave it nothing to do,
    and argparse answers them with a one-line usage — the least useful moment to be terse. So
    walk exactly the subcommand chain the user typed; if every token was a subcommand and the
    parser we landed on still wants something (a nested subcommand, or a required positional),
    that parser's help is the answer.

    Anything else is left to argparse on purpose: a token that is not a subcommand (an option,
    a typo, a half-given positional list) is a genuine mistake, and a specific error about it
    beats a screen of help.
    """
    current = parser
    for token in argv:
        sub = _subparsers_action(current)
        if sub is None or token not in sub.choices:
            return None
        current = sub.choices[token]

    if _subparsers_action(current) is not None:
        return current
    if any(action.required and not action.option_strings for action in current._actions):
        return current
    return None


def main(argv: list[str] | None = None) -> int:
    # Pick up a .env in the CWD so remote commands (files/audit/build) find
    # their URL/token without the caller having to export them first. Real
    # environment variables always win.
    from ada_cli import CliUsageError, load_dotenv_cwd

    load_dotenv_cwd()

    argv = list(sys.argv[1:] if argv is None else argv)
    parser = _build_parser()

    bare = _bare_invocation_parser(parser, argv)
    if bare is not None:
        # stderr, and 2: this is a missing required argument, the same class of mistake argparse
        # already exits 2 for. Full help is the improvement, not a different exit code — and it
        # must not reach a script reading stdout.
        bare.print_help(sys.stderr)
        return 2

    args = parser.parse_args(argv)

    if getattr(args, "needs_ada_logging", False):
        _configure_ada_logging(args.log_level, getattr(args, "log_file", None))

    try:
        rc = args.func(args)
    except CliUsageError as exc:
        print(f"ada {args.command}: error: {exc}", file=sys.stderr)
        return 2
    return rc if isinstance(rc, int) else 0


if __name__ == "__main__":
    sys.exit(main())
