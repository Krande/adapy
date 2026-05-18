"""Single ``ada`` console entry point.

Lives in its own top-level package (``ada_cli``) so that running
``ada --help`` does not trigger ``ada/__init__.py``, which pulls in the
full CAD/FEM surface and adds noticeable startup latency. Each
subcommand imports its implementation lazily, so an invocation only
pays for what it uses.

Subcommand layout:

    ada convert IN OUT                local format conversion
    ada view IN [--renderer ...]      local web viewer
    ada build run|upload|run-and-upload
                                      build artefacts and push to viewer
    ada files list|download           list / download blobs from a scope
    ada serve api|worker              run the REST API / worker process
"""
from __future__ import annotations

import argparse
import sys


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


def _cmd_files_download(args: argparse.Namespace) -> int:
    from ada_cli.files import cmd_download

    return cmd_download(args)


def _cmd_serve_api(_args: argparse.Namespace) -> int:
    from ada.comms.rest.__main__ import run

    run()
    return 0


def _cmd_serve_worker(_args: argparse.Namespace) -> int:
    from ada.comms.rest.worker import run

    run()
    return 0


def _add_convert(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "convert",
        help="Convert between supported CAD/FEM formats (local).",
        description=(
            "Convert an input model to another format. "
            "Input: ifc/step/stp/xml/inp/fem/sat/acis. "
            "Output: ifc/step/stp/gltf/glb/xml/inp."
        ),
    )
    p.add_argument("input", help="Input file path.")
    p.add_argument("output", help="Output file path (format inferred from extension).")
    p.add_argument("--split", action="store_true", help="Split ACIS/SAT bodies into individual faces.")
    p.add_argument("--limit", type=int, default=None, help="Limit number of geometries (debugging).")
    p.set_defaults(func=_cmd_convert, needs_ada_logging=True)


def _add_view(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "view",
        help="Open the built-in web viewer on the given file (local).",
    )
    p.add_argument("input", help="Input file path.")
    p.add_argument("--renderer", default="react", choices=["react", "pygfx", "trimesh"])
    p.add_argument("--host", default="localhost")
    p.add_argument("--ws-port", type=int, default=8765)
    p.add_argument("--split", action="store_true")
    p.add_argument("--limit", type=int, default=None)
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
        help="List or download blobs in a scope (needs ADAPY_VIEWER_URL + ADAPY_VIEWER_TOKEN).",
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
    _add_serve(sub)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if getattr(args, "needs_ada_logging", False):
        import ada

        ada.logger.setLevel(args.log_level)
        ada.logger.propagate = False

    rc = args.func(args)
    return rc if isinstance(rc, int) else 0


if __name__ == "__main__":
    sys.exit(main())
