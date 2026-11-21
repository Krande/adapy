"""Rebuilding a convex-contact run's members: the model(s) its clashes bind in, and the check that the
geometry it measured is still the geometry in storage.

A run (``ada.clash.sidecar``) names its members by GLB element; a builder needs the real members
(beams, plates) to cut. Annotate, detail and making a subject all rebuild them alike -- through the
geometry provider the request names, else one model per part of a group, else the target's own
model -- so a clash that bound when its specs were resolved binds when it is detailed, and again
when a subject is made of the connection.

``storage`` is a scope-bound synchronous facade (``get_bytes(key)``, ``fetch_to_path(key, path)``),
what a job is handed.
"""

from __future__ import annotations

import hashlib
import pathlib
import tempfile

from ada.config import logger

__all__ = [
    "RebuildError",
    "check_geometry_unchanged",
    "load_target_model",
    "rebuild_run_members",
]


class RebuildError(ValueError):
    """A run's members cannot be rebuilt, or its geometry has changed; the message says why."""


def load_target_model(target: dict, storage):
    """The model a run's target names, rebuilt with the members a builder needs.

    A file is read with the full reader (``ada_load._load_with_ada``): its members carry the names the
    viewer GLB was written with (the native IFC -> GLB stream names products as ``ada.from_ifc`` does),
    which is what a run's member names are bound against. A published node is read by its provider.
    """
    kind = target.get("kind")
    if kind == "file":
        from ada.comms.rest.converters.ada_load import _load_with_ada

        source_key = str(target.get("source_key") or "")
        if not source_key:
            raise RebuildError("a file target needs its source_key")
        suffix = pathlib.PurePosixPath(source_key).suffix.lower()
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / f"source{suffix}"
            storage.fetch_to_path(source_key, path)
            return _load_with_ada(path, suffix)
    if kind == "node":
        from ada.clash.from_asset import part_for_asset_node

        return part_for_asset_node(
            collection=str(target["collection"]),
            subject=str(target["subject"]),
            storage=storage,
            revision=target.get("revision") or None,
            node=target.get("node") or None,
        )
    raise RebuildError(f"a clash run over a {kind!r} target cannot be rebuilt into members")


def _load_part_models(run, storage) -> list:
    """One rebuilt model per part of a GROUP run, each by the loader its own target needs; ``None``
    for a part that does not rebuild (its members stay unbound). Raises when no part rebuilds."""
    models, errors = [], []
    for k, part in enumerate(run.parts):
        try:
            models.append(load_target_model(dict(part["source"]), storage))
        except Exception as exc:  # noqa: BLE001 - one model that does not rebuild is not all of them
            logger.warning(f"clash contacts: part {k} ({part.get('key')}) of run {run.run_id} did not rebuild: {exc}")
            models.append(None)
            errors.append(f"part {k}: {type(exc).__name__}: {exc}")
    if all(m is None for m in models):
        raise RebuildError("no model of this group rebuilt: " + "; ".join(errors))
    return models


def _normal_target(target) -> dict:
    """A member target as ``ada.clash.group`` normalises it -- so a run's part source and its group's
    members compare as one identity (an unnamed node IS its subject)."""
    from ada.clash.group import normalise_group

    return normalise_group({"members": [{"target": dict(target)}]})["members"][0]["target"]


def _provider_group_model(group: dict, storage, geometry_provider: str):
    """``group``'s model read from ``geometry_provider``'s publish of the same elements -- the classic
    check's group builder, so a member matched here is matched there. Returns the ``GroupModel``."""
    from ada.clash.group import normalise_group
    from ada.clash.group_model import build_group_model

    return build_group_model(normalise_group(group), storage=storage, geometry_provider=geometry_provider)


def _load_through_provider(run, storage, geometry_provider: str) -> tuple[object, dict]:
    """The model(s) a run's members bind in, read from ``geometry_provider`` rather than each member's
    own provider (``ClashOptions.geometry_provider``'s rule, applied to a stored run), and what to
    record about it: ``{geometry_provider, geometry_remap, geometry_unmatched, geometry_warnings}``.

    A node run is its node re-addressed; a GROUP run rebuilds one model per part, from the group's
    members in that part (or, when none is recorded, the part's whole source) -- each member then
    binds in its own part's model, as without a provider. Raises when nothing rebuilds."""
    from ada.clash.geometry_source import node_group

    remapped: list = []
    unmatched: list = []
    warnings: list[str] = []

    def built(group: dict):
        out = _provider_group_model(group, storage, geometry_provider)
        if out.remap is not None:
            remapped.extend(out.remap.remapped)
            unmatched.extend(out.remap.unmatched)
        warnings.extend(out.warnings)
        return out.model

    if run.target.get("kind") == "group":
        members = list((run.group or {}).get("members") or ())
        models, errors, causes = [], [], []
        for k, part in enumerate(run.parts):
            source = _normal_target(part["source"])
            mine = [m for m in members if _normal_target(m["target"]) == source]
            group = {"name": f"part {k}", "members": mine or [{"target": source}]}
            try:
                models.append(built(group))
            except Exception as exc:  # noqa: BLE001 - one part that does not rebuild is not all of them
                logger.warning(
                    f"clash contacts: part {k} of run {run.run_id} not read from {geometry_provider!r}: {exc}"
                )
                models.append(None)
                causes.append(exc)
                errors.append(f"part {k}: {type(exc).__name__}: {exc}")
        if all(m is None for m in models):
            failure = RebuildError(f"no model of this group was read from {geometry_provider!r}: " + "; ".join(errors))
            failure.parts = tuple(causes)
            raise failure
        model = models
    elif run.target.get("kind") == "node":
        t = run.target
        model = built(
            node_group(
                provider=str(t.get("provider") or ""),
                collection=str(t["collection"]),
                subject=str(t["subject"]),
                revision=str(t.get("revision") or "") or None,
                node=t.get("node") or None,
            )
        )
    else:
        raise RebuildError(f"a {run.target.get('kind')!r} target has no provider to read geometry from")
    return model, {
        "geometry_provider": geometry_provider,
        "geometry_remap": remapped,
        "geometry_unmatched": unmatched,
        **({"geometry_warnings": warnings[:50]} if warnings else {}),
    }


def rebuild_run_members(run, storage, geometry_provider: str | None = None) -> tuple[object, dict]:
    """The model(s) a run's members bind in, and what to record about where they were read from
    (empty without a provider): through ``geometry_provider`` when one is named, else a group run's
    one model per part, else its target's model. A file has no provider: it is ignored for one."""
    if run.target.get("kind") == "file":
        geometry_provider = None
    if geometry_provider:
        return _load_through_provider(run, storage, geometry_provider)
    if run.target.get("kind") == "group":
        return _load_part_models(run, storage), {}
    return load_target_model(dict(run.target), storage), {}


def check_geometry_unchanged(run, storage) -> None:
    """Refuse a run whose measured GLB(s) changed since: its stored clashes would describe geometry
    that is no longer there. A group run checks each of its parts."""
    parts = run.parts or ({"key": run.geometry.get("key"), "sha256": run.geometry.get("sha256")},)
    for part in parts:
        key = str(part.get("key") or "")
        if key and hashlib.sha256(storage.get_bytes(key)).hexdigest() != part.get("sha256"):
            raise RebuildError(
                f"the geometry of this run has changed since it was checked ({key}): run the clash "
                "check again before detailing from it"
            )
