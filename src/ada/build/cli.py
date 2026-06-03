"""ada-build CLI entry point.

``ada-build run`` reads ada_config.toml, runs each declared entrypoint,
writes per-artefact build.json sidecars, and prints a summary.

``ada-build upload`` walks the output dir for produced artefacts and
PUTs each ``(artefact, build.json)`` pair to adapy-viewer's REST API
under ``project:<slug>`` scope, keyed by ``versions/<branch>/<commit>``.

``ada-build run-and-upload`` chains the two for CI use.
"""
from __future__ import annotations

import argparse
import json
import logging
import pathlib
import sys

from ada.build import _git, _runner, _upload

logger = logging.getLogger(__name__)


def _build_json(
    project_id: str,
    entrypoint_name: str,
    artefact_name: str,
    git: _git.GitProvenance,
) -> dict:
    return {
        "schema_version": 1,
        "project_id": project_id,
        "entrypoint": entrypoint_name,
        "artefact": artefact_name,
        "git": git.to_dict(),
    }


def cmd_run(args: argparse.Namespace) -> int:
    config_path = pathlib.Path(args.config).resolve()
    if not config_path.exists():
        print(f"config not found: {config_path}", file=sys.stderr)
        return 2

    repo_root = config_path.parent
    project = _runner.load(config_path)

    selected = (
        [e for e in project.entrypoints if e.name == args.entrypoint]
        if args.entrypoint
        else project.entrypoints
    )
    # ``connections`` is the reserved name for the built-in bake
    # invoked by the [[connections]] block below; it isn't a real
    # user-declared entrypoint so the unknown-entrypoint guard has
    # to let it through. The connections block (later in cmd_run)
    # validates that the project actually has [[connections]] blocks.
    if args.entrypoint and not selected and args.entrypoint != "connections":
        print(f"unknown entrypoint: {args.entrypoint}", file=sys.stderr)
        return 2

    git = _git.extract(repo_root)
    output_root = pathlib.Path(args.output_dir).resolve()

    total = 0
    for entry in selected:
        out_dir = output_root / entry.name
        artefacts = _runner.run_entrypoint(
            entry, project.project_id, repo_root, out_dir
        )
        for a in artefacts:
            sidecar = a.path.with_suffix(a.path.suffix + ".build.json")
            sidecar.write_text(
                json.dumps(
                    _build_json(project.project_id, entry.name, a.name, git),
                    indent=2,
                )
            )
            print(f"  {entry.name}: {a.name} ({a.format}) -> {a.path}")
            print(f"    sidecar: {sidecar}")
        total += len(artefacts)

    # ``[[connections]]`` blocks in ada_config.toml drive a built-in
    # bake step — projects get preview GLBs + manifest.json for every
    # @register_connection spec they declare without writing their
    # own entrypoint. Runs when no ``--entrypoint`` filter was passed
    # (alongside the user entrypoints) or when the filter explicitly
    # targets the synthetic ``connections`` name. Skipped silently
    # when no ``[[connections]]`` blocks were declared.
    run_connections = bool(project.connection_packages) and (
        not args.entrypoint or args.entrypoint == "connections"
    )
    if args.entrypoint == "connections" and not project.connection_packages:
        print(
            "--entrypoint=connections but no [[connections]] blocks in "
            f"{config_path} — nothing to bake.",
            file=sys.stderr,
        )
        return 2
    if run_connections:
        from ada.build import _runner as _r
        from ada.build import connections_bake

        synthetic = _r.EntrypointConfig(
            name="connections",
            callable="ada.build.connections_bake:_bake_with_packages",
        )
        out_dir = output_root / synthetic.name
        out_dir.mkdir(parents=True, exist_ok=True)

        # Stash packages where _bake_with_packages can read them
        # without rebuilding the importlib plumbing inside _run_callable.
        connections_bake._PENDING_PACKAGES = list(project.connection_packages)
        try:
            artefacts = _runner.run_entrypoint(
                synthetic, project.project_id, repo_root, out_dir
            )
        finally:
            connections_bake._PENDING_PACKAGES = []

        for a in artefacts:
            sidecar = a.path.with_suffix(a.path.suffix + ".build.json")
            sidecar.write_text(
                json.dumps(
                    _build_json(project.project_id, synthetic.name, a.name, git),
                    indent=2,
                )
            )
            print(f"  {synthetic.name}: {a.name} ({a.format}) -> {a.path}")
            print(f"    sidecar: {sidecar}")
        total += len(artefacts)

    print(f"\n{total} artefact(s) ready for upload.")
    return 0


def cmd_upload(args: argparse.Namespace) -> int:
    config_path = pathlib.Path(args.config).resolve()
    if not config_path.exists():
        print(f"config not found: {config_path}", file=sys.stderr)
        return 2
    project = _runner.load(config_path)

    cfg = _upload.UploadConfig.from_env()
    if cfg is None:
        print(
            "ADAPY_VIEWER_URL or ADAPY_VIEWER_TOKEN not set — skipping upload.",
            file=sys.stderr,
        )
        return 0

    output_root = pathlib.Path(args.output_dir).resolve()
    if not output_root.exists():
        print(f"output dir not found: {output_root}", file=sys.stderr)
        return 2

    count = _upload.upload_output_dir(output_root, project.project_id, cfg)
    print(f"\n{count} artefact(s) uploaded.")
    return 0


def cmd_run_and_upload(args: argparse.Namespace) -> int:
    rc = cmd_run(args)
    if rc != 0:
        return rc
    return cmd_upload(args)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    parser = argparse.ArgumentParser(prog="ada-build")
    sub = parser.add_subparsers(dest="cmd", required=True)

    def _add_common(p: argparse.ArgumentParser) -> None:
        p.add_argument(
            "--config",
            default="ada_config.toml",
            help="Path to ada_config.toml (default: ./ada_config.toml).",
        )
        p.add_argument(
            "--entrypoint",
            default=None,
            help="Run only the named entrypoint (default: all).",
        )
        p.add_argument(
            "--output-dir",
            default=".ada-build",
            help="Where to stage produced artefacts (default: .ada-build).",
        )

    run = sub.add_parser("run", help="Run entrypoints declared in ada_config.toml")
    _add_common(run)
    run.set_defaults(func=cmd_run)

    upload = sub.add_parser(
        "upload",
        help="Upload artefacts under the output dir to adapy-viewer "
        "(needs ADAPY_VIEWER_URL + ADAPY_VIEWER_TOKEN).",
    )
    _add_common(upload)
    upload.set_defaults(func=cmd_upload)

    run_and_upload = sub.add_parser(
        "run-and-upload",
        help="Chain `run` and `upload` — the CI default.",
    )
    _add_common(run_and_upload)
    run_and_upload.set_defaults(func=cmd_run_and_upload)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
