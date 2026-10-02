"""Build ONE model out of a named group's members -- the half of a group check that opens things.

The request half (parsing, normalising, keying) is ``ada.clash.group``, which the slim api imports;
this module reads files and providers and so runs only where a check runs (a worker, or the
queue-less engine in ``local_jobs``). The check and the detail hand-off both build the group's model
HERE, through the one function below: a joint's id is a hash of its members' names, and a detail
job can only find the joints the check found if it rebuilds the same model the same way.

ONE READ PER SOURCE. Members that share a file (or a published node at one revision) are resolved
against a single read of it, and every selection is found BEFORE any of them is moved: moving one
re-parents it out of the tree the next one's path is matched against. A member inside another
member of the same source is checked once, as part of the larger one, and said so in a warning --
two copies of one beam would meet themselves at every node.

A MEMBER THAT CANNOT BE FOUND IS A WARNING, NOT A FAILURE. A group is often assembled from views of
several models, and one of them moving on is no reason to withhold the joints among the rest. Only
a group where nothing resolved fails, because a check over an empty model would answer "no joints"
to a question that was never asked.
"""

from __future__ import annotations

import contextlib
import pathlib
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from ada.clash.group import member_label, source_identity

__all__ = ["GroupModel", "GroupModelError", "build_group_model", "clash_check_group", "geometry_provenance"]


class GroupModelError(ValueError):
    """No member of the group could be resolved, so there is no model to check."""


@dataclass
class GroupModel:
    #: An ``ada.Assembly`` named after the group, holding one part per resolved member, named
    #: ``"<n>: <label>"`` with ``n`` the member's 1-based position in the normalised group -- so a
    #: part keeps its name when another member fails to resolve.
    model: Any
    warnings: list[str] = field(default_factory=list)
    resolved: int = 0
    #: The group the model was built FROM: the one given, or -- with a geometry provider -- the
    #: members re-addressed to it (``ada.clash.geometry_source``). A detail job rebuilds from this.
    group: dict = field(default_factory=dict)
    #: The re-addressing, when a geometry provider was asked for; None otherwise.
    remap: Any = None


def _describe(target: Mapping[str, Any]) -> str:
    if target.get("kind") == "file":
        return f"file {target['source_key']!r}"
    rev = target.get("revision") or "latest"
    return f"node {target['collection']}/{target['subject']}@{rev} ({target.get('node') or target['subject']})"


def _read_file(storage: Any, source_key: str, *, members_only: bool, load_file: Callable) -> Any:
    from ada.cadit.ifc.read.native_members import load_members_or_model
    from ada.core.file_system import new_temp_path

    ext = pathlib.PurePosixPath(source_key).suffix.lower()
    tmp = new_temp_path(suffix=ext or None)
    try:
        storage.fetch_to_path(source_key, tmp)
        if members_only:
            # The file form's own reader (formats/clash_check.py): members natively where it can.
            # Its model is FLAT, though -- one part of members, none of the file's tree -- so a
            # member naming an element inside the file needs the full reader to find it by path.
            return load_members_or_model(tmp, ext, load_file)
        return load_file(tmp, ext)
    finally:
        with contextlib.suppress(OSError):
            tmp.unlink(missing_ok=True)


def _ancestor_ids(obj: Any) -> set[int]:
    return {id(a) for a in obj.get_ancestors(include_self=True) if a is not obj}


def _place(obj: Any, holder: Any) -> None:
    """Move a resolved member under ``holder``, keeping it where its source placed it."""
    import ada
    from ada.comms.rest.selection_export import reparent_selection

    if isinstance(obj, ada.Assembly):
        # A whole file: its parts and any objects on its root move, the Assembly itself does not.
        # Nested as a part, it would answer `get_assembly()` for everything under it, and units,
        # layers and the IFC store would resolve against a model that is no longer the root.
        for child in list(obj.parts.values()):
            reparent_selection(child, holder)
        for direct in list(obj.get_all_physical_objects(sub_elements_only=True)):
            holder.add_object(direct)
        return
    reparent_selection(obj, holder)


def build_group_model(
    group: Mapping[str, Any],
    *,
    storage: Any,
    load_file: Callable | None = None,
    read_node: Callable[..., Any] | None = None,
    geometry_provider: str | None = None,
) -> GroupModel:
    """The combined model of a NORMALISED group (``ada.clash.group.normalise_group``).

    ``storage`` is the synchronous, scope-bound facade a worker hands a provider (``fetch_to_path``,
    ``get_bytes``, ``list_keys``). ``load_file(path, ext)`` reads a file into a model, defaulting to
    the reader every source-backed job shares; ``read_node(collection=, subject=, storage=,
    revision=, node=)`` reads a published node, defaulting to its provider's concepts. Both are
    parameters so a test can stand in for either without a storage backend.

    ``geometry_provider`` re-addresses every node member to that provider's publish of the same
    element BEFORE anything is read (``ada.clash.geometry_source.remap_group``); the members it
    cannot match are warnings, like a member that cannot be found. ``GroupModel.group`` is then the
    re-addressed group, which is what a detail job has to rebuild from.
    """
    import ada
    from ada.comms.rest.selection_export import SelectionExportError, find_selection

    remap = None
    asked = len(group.get("members") or ())
    if geometry_provider:
        from ada.clash.geometry_source import remap_group

        remap = remap_group(group, geometry_provider, storage=storage)
        group = remap.group
        if not group["members"]:
            detail = "; ".join(remap.warnings[:5]) or "no members"
            raise GroupModelError(
                f"none of the {asked} member(s) of this group could be matched to provider "
                f"{geometry_provider!r}: {detail}"
            )

    if load_file is None:
        from ada.comms.rest.converters.ada_load import _load_with_ada

        load_file = _load_with_ada
    if read_node is None:
        from ada.clash.from_asset import part_for_asset_node

        read_node = part_for_asset_node

    members = list(group.get("members") or ())
    warnings: list[str] = list(remap.warnings) if remap is not None else []

    by_source: dict[tuple, list[tuple[int, Mapping[str, Any]]]] = {}
    for n, member in enumerate(members, start=1):
        by_source.setdefault(source_identity(member["target"]), []).append((n, member))

    placed: dict[int, tuple[Mapping[str, Any], Any]] = {}
    for entries in by_source.values():
        target = entries[0][1]["target"]
        whole = [e for e in entries if not e[1].get("element")]
        try:
            if target["kind"] == "file":
                model = _read_file(storage, target["source_key"], members_only=bool(whole), load_file=load_file)
            else:
                model = read_node(
                    collection=target["collection"],
                    subject=target["subject"],
                    storage=storage,
                    revision=target.get("revision"),
                    node=target.get("node"),
                )
        except Exception as exc:  # noqa: BLE001 - one unreadable source is not the group's failure
            numbers = ", ".join(str(n) for n, _ in entries)
            warnings.append(
                f"{_describe(target)} could not be read ({type(exc).__name__}: {exc}); member(s) {numbers} left out"
            )
            continue

        if whole:
            n_whole, m_whole = whole[0]
            for n, _m in entries:
                if n != n_whole:
                    warnings.append(
                        f"member {n} is inside member {n_whole} (the whole of {_describe(target)}); checked once"
                    )
            placed[n_whole] = (m_whole, model)
            continue

        resolved: list[tuple[int, Mapping[str, Any], Any]] = []
        for n, m in entries:
            try:
                obj = find_selection(model, m["element"], m.get("path") or ())
            except SelectionExportError as exc:
                warnings.append(f"member {n} ({member_label(m)}) in {_describe(target)}: {exc}")
                continue
            resolved.append((n, m, obj))

        chosen: dict[int, int] = {}  # id(obj) -> member number
        for n, _m, obj in resolved:
            chosen.setdefault(id(obj), n)
        for n, m, obj in resolved:
            if chosen[id(obj)] != n:
                warnings.append(f"member {n} is the same element as member {chosen[id(obj)]}; checked once")
                continue
            outer = next((chosen[a] for a in _ancestor_ids(obj) if a in chosen), None)
            if outer is not None:
                warnings.append(f"member {n} is inside member {outer}; checked once")
                continue
            placed[n] = (m, obj)

    if not placed:
        detail = "; ".join(warnings[:5]) or "no members"
        raise GroupModelError(f"none of the {asked} member(s) of this group could be resolved: {detail}")

    asm = ada.Assembly(str(group.get("name") or "group"))
    # Moved only now, after every source's selections were found: see the module docstring.
    for n in sorted(placed):
        member, obj = placed[n]
        holder = ada.Part(f"{n}: {member_label(member)}")
        asm.add_part(holder)
        _place(obj, holder)
    return GroupModel(model=asm, warnings=warnings, resolved=len(placed), group=dict(group), remap=remap)


def geometry_provenance(built: GroupModel, requested: Mapping[str, Any], geometry_provider: str | None) -> dict:
    """What a result records about WHERE its geometry came from: the provider asked for, the group
    as requested, and each member re-addressed (``from`` -> ``to``). Empty without a provider, so a
    default check's document is unchanged."""
    if not geometry_provider or built.remap is None:
        return {}
    return {
        "geometry_provider": geometry_provider,
        "requested_group": dict(requested),
        "geometry_remap": list(built.remap.remapped),
        "geometry_unmatched": list(built.remap.unmatched),
    }


def clash_check_group(
    group: Mapping[str, Any],
    *,
    token: str,
    storage: Any,
    options=None,
    capability_of: Callable | None = None,
    provenance: Mapping[str, Any] | None = None,
    load_file: Callable | None = None,
    read_node: Callable[..., Any] | None = None,
) -> dict:
    """Build the group's model and check it; the ``ada.clash/result@2`` document as a dict.

    Transport-free, so the worker's ``clash_check_group`` handler and the queue-less engine in
    ``local_jobs`` run the identical check. ``provenance.group`` is the group exactly as given
    -- normalised, node revisions resolved -- because a detail job rebuilds the model from it. With
    ``options.geometry_provider`` it is the group AS CHECKED, every member re-addressed to that
    provider, and ``provenance.requested_group`` is the one given: the detail job then rebuilds the
    same model from the recorded members without searching again.
    """
    from dataclasses import replace

    from ada.clash.builtin_specs import register_builtin_specs
    from ada.clash.group import GROUP_SOURCE_PREFIX
    from ada.clash.identify import run_clash_check

    geometry_provider = getattr(options, "geometry_provider", None)
    built = build_group_model(
        group,
        storage=storage,
        load_file=load_file,
        read_node=read_node,
        geometry_provider=geometry_provider,
    )
    register_builtin_specs()
    result = run_clash_check(
        built.model,
        source_key=f"{GROUP_SOURCE_PREFIX}{token}",
        options=options,
        capability_of=capability_of,
        provenance={
            **dict(provenance or {}),
            "reader": "group",
            "group": built.group,
            "members_resolved": built.resolved,
            **geometry_provenance(built, group, geometry_provider),
        },
    )
    # The group's own warnings first: a member left out changes what every count below means.
    return replace(result, warnings=(*built.warnings, *result.warnings)).to_dict()
