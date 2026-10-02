"""A NAMED GROUP of members, checked together as one model -- the request half, and stdlib-only.

WHY A GROUP AND NOT A LIST OF CHECKS. A check over one file cannot find a joint whose two members
live in two files: the beam pass meets members at a shared node, and a node only exists once both
members are in the same model. So a group is not N checks merged afterwards, it is one check over
a model assembled from every member first (``ada.clash.group_model``).

WHY THIS MODULE IS SEPARATE FROM THE ONE THAT BUILDS THE MODEL. The REST route parses, validates
and keys a group before anything is enqueued, and that route runs in the SLIM api, which has no
numpy and no reader (see ``ada.clash.options`` for the same argument). Everything here is
therefore plain dicts and ``json``; ``group_model`` is where ``ada`` objects appear.

THE NORMALISED FORM IS THE IDENTITY. Members are sorted and deduplicated, ``null`` and absent mean
the same thing, and a node with no ``node`` names its subject -- so two requests that mean the same
group produce the same bytes, the same token and therefore the same cached result.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

__all__ = [
    "GROUP_SOURCE_PREFIX",
    "MAX_GROUP_MEMBERS",
    "GroupError",
    "group_derived_prefix",
    "group_token",
    "member_label",
    "normalise_group",
    "source_identity",
]

#: A group of this many members is already a plant area. The ceiling is not about the check's
#: cost (one large file can hold far more members than this) but about the request: every member
#: may cost the route a ``head()`` or a manifest read before anything is enqueued.
MAX_GROUP_MEMBERS = 500

#: What a group result's ``source_key`` starts with. Not a storage key -- a result over a group
#: has no single source -- and spelled so nothing can mistake it for one and try to download it.
GROUP_SOURCE_PREFIX = "group:"

_TARGET_KINDS = ("file", "node")


class GroupError(ValueError):
    """The group cannot be checked as sent, with the reason a caller can act on."""


def _str(raw: Any, what: str, *, required: bool) -> str | None:
    if raw is None or raw == "":
        if required:
            raise GroupError(f"{what} is required")
        return None
    if not isinstance(raw, str):
        raise GroupError(f"{what} must be a string, got {type(raw).__name__}")
    value = raw.strip()
    if required and not value:
        raise GroupError(f"{what} is required")
    return value or None


def _target(raw: Any, where: str) -> dict:
    if not isinstance(raw, Mapping):
        raise GroupError(f"{where}.target must be an object")
    kind = raw.get("kind")
    if kind == "file":
        return {"kind": "file", "source_key": _str(raw.get("source_key"), f"{where}.target.source_key", required=True)}
    if kind == "node":
        subject = _str(raw.get("subject"), f"{where}.target.subject", required=True)
        return {
            "kind": "node",
            "provider": _str(raw.get("provider"), f"{where}.target.provider", required=False) or "",
            "collection": _str(raw.get("collection"), f"{where}.target.collection", required=True),
            "subject": subject,
            "revision": _str(raw.get("revision"), f"{where}.target.revision", required=False),
            # The node check's own default (routes/clash_check.py): an unnamed node IS the subject.
            # Folded here so the two spellings are one identity rather than two cache entries.
            "node": _str(raw.get("node"), f"{where}.target.node", required=False) or subject,
        }
    raise GroupError(f"{where}.target.kind must be one of {list(_TARGET_KINDS)}, got {kind!r}")


def _member(raw: Any, index: int) -> dict:
    where = f"members[{index}]"
    if not isinstance(raw, Mapping):
        raise GroupError(f"{where} must be an object")
    target = _target(raw.get("target"), where)
    element = _str(raw.get("element"), f"{where}.element", required=False)
    path_raw = raw.get("path")
    if path_raw is None:
        path_raw = []
    if isinstance(path_raw, (str, bytes)) or not isinstance(path_raw, (list, tuple)):
        raise GroupError(f"{where}.path must be a list of names")
    path = [str(p) for p in path_raw]
    if element is None:
        # A whole source has no position in its own tree; a stray path would only split one
        # identity into two cache entries.
        path = []
    elif path and path[-1] != element:
        # The same refusal `selection_export.find_selection` makes, made here so it is a 400 on the
        # request rather than a warning two polls later.
        raise GroupError(f"{where}.path {path!r} does not end at its element {element!r}")
    return {"target": target, "element": element, "path": path}


def _sort_key(member: Mapping[str, Any]) -> str:
    return json.dumps(member, sort_keys=True)


def normalise_group(raw: Any) -> dict:
    """``{"name", "members"}`` with every member validated, sorted and deduplicated.

    Raises :class:`GroupError` for anything that could not be checked as sent -- an empty group, too
    many members, an unknown target kind, a path that does not end at its element.
    """
    if not isinstance(raw, Mapping):
        raise GroupError("'group' must be an object")
    name = _str(raw.get("name"), "group.name", required=False) or "group"
    members_raw = raw.get("members")
    if not isinstance(members_raw, (list, tuple)) or not members_raw:
        raise GroupError("group.members must be a non-empty list")
    if len(members_raw) > MAX_GROUP_MEMBERS:
        raise GroupError(f"a group holds at most {MAX_GROUP_MEMBERS} members, got {len(members_raw)}")
    unique = {_sort_key(m): m for m in (_member(m, i) for i, m in enumerate(members_raw))}
    return {"name": name, "members": [unique[k] for k in sorted(unique)]}


def source_identity(target: Mapping[str, Any]) -> tuple:
    """What one READ of a source is keyed by: members sharing it are read from one model.

    A file is its key; a node is everything the provider is asked for, revision included, since
    two revisions of one subject are two models.
    """
    if target.get("kind") == "file":
        return ("file", str(target["source_key"]))
    return (
        "node",
        str(target["collection"]),
        str(target["subject"]),
        target.get("revision"),
        target.get("node"),
    )


def member_label(member: Mapping[str, Any]) -> str:
    """A short human name for a member, used for its part in the combined model."""
    if member.get("element"):
        return str(member["element"])
    target = member["target"]
    if target.get("kind") == "file":
        return str(target["source_key"]).rstrip("/").rsplit("/", 1)[-1]
    return str(target.get("node") or target["subject"])


def group_token(members: list[Mapping[str, Any]], content_tokens: Mapping[str, str]) -> str:
    """The identity of a group check's MODEL: its normalised members plus what each source held.

    ``content_tokens`` is per source -- a file's head token, a node's resolved revision -- so a
    re-upload or a re-publish moves the token and the cached result stops being served. The group's
    NAME is deliberately not in it: renaming a group changes nothing about the model checked.
    """
    identity = {"members": list(members), "content": dict(sorted(content_tokens.items()))}
    return hashlib.sha256(json.dumps(identity, sort_keys=True).encode("utf-8")).hexdigest()[:16]


def group_derived_prefix(token: str, options_token: str) -> str:
    return f"_derived/clash/group/{token}/{options_token}"
