"""ada-build CLI entry point.

``ada-build run`` reads ada_config.toml, runs each declared entrypoint,
writes per-artefact build.json sidecars, and prints a summary.
"""
from __future__ import annotations

import argparse
import json
import logging
import pathlib
import sys

from ada.build import _git, _runner

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
    if args.entrypoint and not selected:
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

    print(f"\n{total} artefact(s) ready for upload.")
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    parser = argparse.ArgumentParser(prog="ada-build")
    sub = parser.add_subparsers(dest="cmd", required=True)

    run = sub.add_parser("run", help="Run entrypoints declared in ada_config.toml")
    run.add_argument(
        "--config",
        default="ada_config.toml",
        help="Path to ada_config.toml (default: ./ada_config.toml).",
    )
    run.add_argument(
        "--entrypoint",
        default=None,
        help="Run only the named entrypoint (default: all).",
    )
    run.add_argument(
        "--output-dir",
        default=".ada-build",
        help="Where to stage produced artefacts (default: .ada-build).",
    )
    run.set_defaults(func=cmd_run)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
