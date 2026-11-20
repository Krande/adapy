"""Read-only views of what the clash pipeline already stored, for an agent rather than the panel.

Nothing here computes anything new: a convex-contact run (``ada.clash.sidecar``) already holds every
joint's members, measured contact and applicable specs; a detail model's take-off
(``result.stats.json``, written by ``ada.clash.detail.build_detail``) already holds what was built,
what was not and why, and which welds failed. These functions put the two side by side per joint, so
one answer says "joint X: members A and B, declined by spec S because ..." without the reader
knowing the documents' keys or shapes.

LIGHT ON PURPOSE: stdlib plus ``ada.clash.sidecar``, as the routes that use it.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from ada.clash.sidecar import ContactRecord, ContactRun

__all__ = ["contact_view", "detail_view", "joint_status", "newest"]


def newest(lines: Iterable[Mapping[str, Any]]) -> dict | None:
    """The newest of some run index lines (``created_at``, ISO text), or None for none."""
    best = None
    for line in lines:
        if best is None or str(line.get("created_at", "")) > str(best.get("created_at", "")):
            best = line
    return dict(best) if best is not None else None


def _centre(c: ContactRecord) -> list[float]:
    return [round(v, 6) for v in c.centre]


def contact_view(run: ContactRun, c: ContactRecord) -> dict:
    """One clash of a run as an agent reads it: its members by index, element id and name (and role,
    where the run's annotate resolved one), where it is, what was measured, and which specs apply."""
    roles = {} if c.roles is None else {int(v): str(k) for k, v in c.roles.items()}
    members = []
    for idx in c.members:
        m = run.members[idx]
        member = {"index": idx, "id": str(m.get("id")), "name": str(m.get("name")), "role": roles.get(idx)}
        for k in ("element", "part"):
            if m.get(k) is not None:
                member[k] = m[k]
        members.append(member)
    return {
        "id": c.id,
        "members": members,
        "centre": _centre(c),
        "normal": list(c.normal),
        "distance": c.distance,
        "contact_area": c.contact_area,
        "applicable": sorted(
            (dict(a) for a in c.applicable), key=lambda a: (-float(a.get("priority") or 0), str(a.get("spec")))
        ),
    }


def _records(stats: Mapping[str, Any], field: str) -> list[dict]:
    raw = stats.get(field)
    return [dict(r) for r in raw if isinstance(r, Mapping)] if isinstance(raw, list) else []


def joint_status(joint_id: str, stats: Mapping[str, Any] | None, requested: Iterable[str] | None) -> str:
    """``built``, ``declined``, ``failed``, ``pending`` (no take-off yet), ``not_requested`` or
    ``unknown`` (a take-off from before connections were recorded that does not name it)."""
    if stats is None:
        return "pending" if requested is None or joint_id in set(requested) else "not_requested"
    for nb in _records(stats, "not_built"):
        if nb.get("joint_id") == joint_id:
            return "declined" if nb.get("kind") == "declined" else "failed"
    if any(c.get("joint_id") == joint_id for c in _records(stats, "connections")):
        return "built"
    if requested is not None and joint_id not in set(requested):
        return "not_requested"
    # An older take-off: its items are named `<spec>_<joint id>` (`ada.clash.detail._build_with`).
    joints = stats.get("joints") if isinstance(stats.get("joints"), Mapping) else {}
    items = [i for i in (joints.get("items") or []) if isinstance(i, Mapping)]
    if any(str(i.get("name", "")).endswith(f"_{joint_id}") or f"_{joint_id}_" in str(i.get("name", "")) for i in items):
        return "built"
    return "unknown"


def detail_view(
    record: Mapping[str, Any],
    stats: Mapping[str, Any] | None,
    run: ContactRun | None,
    joint_ids: Iterable[str] | None = None,
) -> dict:
    """A detail model per joint: for each joint asked about (default: every joint it was asked to
    build, else every joint its take-off names), its status, the connections built for it with their
    welds, why it was not built, which of its welds failed, and -- from the run it was detailed from,
    when that is a convex-contact run still stored -- its members and contact. A declined joint is
    in no GLB; this is where its members are still named."""
    requested = record.get("joint_ids")
    requested = [str(j) for j in requested] if isinstance(requested, list) else None
    connections = _records(stats or {}, "connections")
    not_built = _records(stats or {}, "not_built")
    weld_failures = _records(stats or {}, "weld_failures")
    if joint_ids is not None:
        ids = list(dict.fromkeys(str(j) for j in joint_ids))
    elif requested is not None:
        ids = requested
    else:
        ids = list(dict.fromkeys([*(c.get("joint_id") for c in connections), *(n.get("joint_id") for n in not_built)]))
        ids = [str(i) for i in ids if i]

    joints = []
    for jid in ids:
        contact = run.contact(jid) if run is not None else None
        joints.append(
            {
                "joint_id": jid,
                "status": joint_status(jid, stats, requested),
                "connections": [c for c in connections if c.get("joint_id") == jid],
                "not_built": next((n for n in not_built if n.get("joint_id") == jid), None),
                "weld_failures": [w for w in weld_failures if w.get("joint_id") == jid],
                "contact": contact_view(run, contact) if contact is not None else None,
            }
        )
    takeoff = (stats or {}).get("joints")
    return {
        "model": dict(record),
        "takeoff": dict(takeoff) if isinstance(takeoff, Mapping) else None,
        "built_by": (stats or {}).get("built_by"),
        "has_connection_records": bool(stats) and "connections" in stats,
        "joints": joints,
    }
