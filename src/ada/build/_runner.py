"""ada_config.toml reader and entrypoint dispatcher."""
from __future__ import annotations

import importlib
import logging
import pathlib
import subprocess
import sys
from dataclasses import dataclass

try:
    import tomllib  # Python 3.11+
except ImportError:  # pragma: no cover
    import tomli as tomllib  # type: ignore[no-redef]

from ada.build import _FORMAT_BY_SUFFIX, BuildArtefact, BuildContext, _active_context

logger = logging.getLogger(__name__)


@dataclass
class EntrypointConfig:
    name: str
    callable: str | None = None
    script: str | None = None
    artefacts: list[str] | None = None


@dataclass
class ProjectConfig:
    project_id: str
    display_name: str
    entrypoints: list[EntrypointConfig]
    connection_packages: list[str]
    """Importable module paths declared in ``[[connections]]`` blocks
    of ``ada_config.toml``. The CLI's ``run`` step invokes
    ``ada.build.connections_bake.bake(packages)`` once these are
    discovered — no per-project bake script needed."""


def load(config_path: pathlib.Path) -> ProjectConfig:
    with config_path.open("rb") as f:
        raw = tomllib.load(f)

    project = raw["project"]
    entries = []
    for ep in raw.get("entrypoint", []):
        if "callable" not in ep and "script" not in ep:
            raise ValueError(
                f"entrypoint {ep.get('name')!r} must declare either "
                f"'callable' or 'script'"
            )
        entries.append(
            EntrypointConfig(
                name=ep["name"],
                callable=ep.get("callable"),
                script=ep.get("script"),
                artefacts=ep.get("artefacts"),
            )
        )

    connection_packages: list[str] = []
    for block in raw.get("connections", []):
        pkg = block.get("package")
        if not isinstance(pkg, str) or not pkg.strip():
            raise ValueError(
                "[[connections]] block must declare 'package' (a dotted module path "
                "whose import fires @register_connection decorators)"
            )
        connection_packages.append(pkg.strip())

    return ProjectConfig(
        project_id=project["id"],
        display_name=project.get("display_name", project["id"]),
        entrypoints=entries,
        connection_packages=connection_packages,
    )


def run_entrypoint(
    entry: EntrypointConfig,
    project_id: str,
    repo_root: pathlib.Path,
    output_dir: pathlib.Path,
) -> list[BuildArtefact]:
    """Run a single entrypoint and return its registered artefacts."""
    output_dir.mkdir(parents=True, exist_ok=True)

    if entry.callable:
        return _run_callable(entry, project_id, repo_root, output_dir)
    return _run_script(entry, repo_root, output_dir)


def _run_callable(
    entry: EntrypointConfig,
    project_id: str,
    repo_root: pathlib.Path,
    output_dir: pathlib.Path,
) -> list[BuildArtefact]:
    module_path, _, func_name = entry.callable.partition(":")
    if not func_name:
        raise ValueError(
            f"callable for entrypoint {entry.name!r} must be 'module.path:func'"
        )

    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    module = importlib.import_module(module_path)
    func = getattr(module, func_name)

    ctx = BuildContext(
        project_id=project_id,
        entrypoint_name=entry.name,
        output_dir=output_dir,
    )
    token = _active_context.set(ctx)
    try:
        func()
    finally:
        _active_context.reset(token)
    return ctx.artefacts


def _run_script(
    entry: EntrypointConfig,
    repo_root: pathlib.Path,
    output_dir: pathlib.Path,
) -> list[BuildArtefact]:
    if not entry.artefacts:
        raise ValueError(
            f"entrypoint {entry.name!r} in script mode must declare 'artefacts'"
        )
    script_path = repo_root / entry.script
    subprocess.run(
        [sys.executable, str(script_path)], cwd=repo_root, check=True
    )

    artefacts: list[BuildArtefact] = []
    for rel in entry.artefacts:
        p = (repo_root / rel).resolve()
        if not p.exists():
            raise FileNotFoundError(
                f"declared artefact missing after {entry.name}: {p}"
            )
        fmt = _FORMAT_BY_SUFFIX.get(
            p.suffix.lower(), p.suffix.lstrip(".") or "bin"
        )
        artefacts.append(BuildArtefact(path=p, name=p.name, format=fmt))
    return artefacts
