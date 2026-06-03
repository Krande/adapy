"""Built-in ada-build entrypoint: bake preview GLBs for registered
ConnectionSpecs declared via ``[[connections]]`` blocks in
``ada_config.toml``.

Replaces per-project bake scripts. The toml-level declaration::

    [[connections]]
    package = "my_project.connections"

makes ada-build:

  1. ``importlib.import_module("my_project.connections")`` so the
     package's ``@register_connection`` decorators fire.
  2. Iterate ``ada.api.connections.all_registered()`` and bake each
     spec that declares ``defaults`` via ``build_component`` +
     ``Connection.to_gltf``.
  3. Write ``<spec>.glb`` + ``manifest.json`` into the active
     ``BuildContext.output_dir`` and call ``ada.build.publish()``
     for each.

The bake is invoked by the CLI's run step when the loaded project
has at least one ``[[connections]]`` block — no per-project
boilerplate required.
"""

from __future__ import annotations

import importlib
import json
import logging
from dataclasses import asdict, is_dataclass
from typing import Any

import ada.build as ada_build

logger = logging.getLogger(__name__)


# Set by ``cli.cmd_run`` just before invoking ``_bake_with_packages``
# via the synthetic entrypoint dispatcher. The dispatcher reads from
# here because the EntrypointConfig dataclass doesn't carry arbitrary
# kwargs — kept module-level (not a thread-local) since ada-build runs
# entrypoints sequentially on the main thread.
_PENDING_PACKAGES: list[str] = []


def _bake_with_packages() -> None:
    """Adapter for the synthetic ``[[connections]]`` entrypoint.

    Reads ``_PENDING_PACKAGES`` (set by cli.cmd_run before dispatch)
    and delegates to ``bake``. Separate function so the standard
    ``module:func`` entrypoint dispatch path works without any
    special-casing in _runner.run_entrypoint.
    """
    if not _PENDING_PACKAGES:
        raise RuntimeError(
            "ada.build.connections_bake._bake_with_packages called without "
            "_PENDING_PACKAGES set — call bake(packages) directly or invoke "
            "via the cli's [[connections]] dispatch."
        )
    bake(list(_PENDING_PACKAGES))


def bake(packages: list[str]) -> None:
    """Discover registered specs across ``packages`` and bake each one.

    Runs in an active ``ada-build`` context — ``current_context()``'s
    ``output_dir`` is where artefacts land; ``publish()`` registers
    them for upload.
    """
    from ada.api.connections import all_registered, build_component
    from ada.api.connections.spec import spec_to_form_schema

    for module_path in packages:
        logger.info("connections-bake: importing %s", module_path)
        importlib.import_module(module_path)

    ctx = ada_build.current_context()
    if ctx is None:
        raise RuntimeError(
            "ada.build.connections_bake.bake() must run inside an "
            "ada-build context; invoke via `ada-build run`."
        )

    out_dir = ctx.output_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    registry = all_registered()
    logger.info("connections-bake: %d registered specs across %d package(s)",
                len(registry), len(packages))

    manifest: dict[str, Any] = {"specs": {}}
    capability = _resolve_capability(packages)
    if capability:
        manifest["capability"] = capability

    for reg in registry:
        spec = reg.spec
        name = spec.name
        if spec.defaults is None:
            logger.info("  skip %s: no defaults declared", name)
            continue
        try:
            conn = build_component(name, spec.defaults)
        except Exception as ex:  # noqa: BLE001 — keep one bad spec from killing the batch
            logger.warning(
                "  fail %s: build_component raised %s: %s",
                name, type(ex).__name__, ex,
            )
            continue

        glb_path = out_dir / f"{name}.glb"
        try:
            conn.to_gltf(glb_path)
        except Exception as ex:  # noqa: BLE001
            logger.warning(
                "  fail %s: to_gltf raised %s: %s", name, type(ex).__name__, ex,
            )
            continue

        manifest["specs"][name] = {
            "schema": _schema_for_json(spec_to_form_schema(spec)),
            "defaults": spec.defaults,
            "preview_glb": glb_path.name,
            "tags": sorted(spec.tags),
            "priority": spec.priority,
            "beams": len(list(conn.beams)),
            "welds": len(conn.welds),
            "plates": len(list(conn.plates)),
        }
        ada_build.publish(glb_path)
        logger.info(
            "  ok   %s: beams=%d welds=%d plates=%d -> %s",
            name, len(list(conn.beams)), len(conn.welds), len(list(conn.plates)),
            glb_path.name,
        )

    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True))
    ada_build.publish(manifest_path)
    logger.info(
        "connections-bake: wrote %d spec(s) to %s",
        len(manifest["specs"]), manifest_path,
    )


def _resolve_capability(packages: list[str]) -> str | None:
    """Worker-pool capability tag for the build endpoint to route
    component_build jobs to.

    Read from the env var ``ADA_COMPONENT_CAPABILITY`` so the bake
    project can override per-environment without baking it into the
    declared package. None when unset.
    """
    import os

    cap = os.environ.get("ADA_COMPONENT_CAPABILITY", "").strip()
    return cap or None


def _serialise(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, (set, frozenset)):
        return sorted(value)
    return value


def _schema_for_json(schema: dict) -> dict:
    out: dict = {}
    for k, v in schema.items():
        if isinstance(v, list):
            out[k] = [
                {ik: _serialise(iv) for ik, iv in role.items()} if isinstance(role, dict) else role
                for role in v
            ]
        else:
            out[k] = _serialise(v)
    return out
