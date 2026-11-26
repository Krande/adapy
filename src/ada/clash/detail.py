"""Re-find named joints in a reloaded model and build them with a registered spec -- the one loop
both ``clash_detail`` engines run (the worker's ``formats/clash_detail.py`` and the queue-less
``local_jobs.start_clash_detail``).

It used to be written out in each of them. The two copies had to agree on every argument a builder
receives -- the same job kind must not mean two different things depending on where it ran -- and
the only way that stays true is for there to be one copy. How the model is REOPENED is what differs
between a file, a published node and a group, and that stays with the caller.
"""

from __future__ import annotations

import hashlib
import time
from typing import Any, Callable, Mapping

from ada.clash._seq import as_items

__all__ = [
    "DETAIL_SHARES",
    "DetailReporter",
    "JointNotBuilt",
    "build_detail",
    "build_joint",
    "connection_records",
    "dedup_seams",
    "detail_progress",
    "found_id",
    "joints_by_id",
    "stage_progress",
    "upload_stage",
]

#: ``on_progress(done, total)``: called by :func:`build_detail` before the first joint and after each.
DetailProgress = Callable[[int, int], None]


def found_id(found) -> str:
    """The exact formula ``run_clash_check`` stamps a joint's id with -- a documented, deterministic
    PUBLIC contract (``ada.clash.identify.run_clash_check``'s own docstring), re-derived here so a
    cached id can be matched back up without ``identify`` exposing an id-taking entrypoint."""
    from ada.clash.classify import describe_member

    names = sorted(describe_member(m).name for m in found.members)
    return hashlib.sha256("|".join([found.origin, *names]).encode("utf-8")).hexdigest()[:12]


def joints_by_id(model, options) -> dict[str, Any]:
    """Identify ``model`` at ``options`` and key the raw contacts by the id the check gave them."""
    from ada.clash.builtin_specs import register_builtin_specs
    from ada.clash.identify import identify_joints

    register_builtin_specs()
    return {found_id(f): f for f in identify_joints(model, options).joints}


def stage_progress(
    report: Callable[[str, float], None],
    *,
    stage: str = "detail",
    start: float = 0.55,
    end: float = 0.85,
    min_interval_s: float = 1.0,
    clock: Callable[[], float] = time.monotonic,
) -> DetailProgress:
    """An ``on_progress(done, total)`` for :func:`build_detail` that reports ``"<stage> done/total"``
    to a job's ``report(stage, fraction)``, the fraction moving from ``start`` to ``end`` over the
    joints. At most one report per ``min_interval_s``: a joint can take milliseconds and every report
    is a queue write. The first and the last are always reported."""
    last: list[float | None] = [None]

    def _on_progress(done: int, total: int) -> None:
        now = clock()
        if 0 < done < total and last[0] is not None and now - last[0] < min_interval_s:
            return
        last[0] = now
        frac = start + (end - start) * (done / total if total else 1.0)
        report(f"{stage} {done}/{total}", round(frac, 4))

    return _on_progress


#: ``on_stage(name, done, total)``: a phase of :func:`build_detail` after the joints, with its count
#: (``total`` 0 for a phase that is one step).
DetailStage = Callable[[str, int, int], None]

#: The share of a detail job's progress each phase takes, (start, end); the job wrappers' uploads
#: follow at :data:`UPLOAD_SHARE`. A phase's label is what the viewer shows as the job's stage.
DETAIL_SHARES: dict[str, tuple[float, float]] = {
    "detail": (0.55, 0.75),
    "bodies": (0.75, 0.82),
    "tessellating": (0.82, 0.93),
    "writing GLB": (0.93, 0.95),
    "stats": (0.95, 0.95),
}
UPLOAD_SHARE = (0.95, 0.99)


class DetailReporter:
    """A detail job's progress through every phase, reported to its ``report(stage, fraction)``:
    the joints (``"detail 120/392"``, :meth:`__call__` -- the ``on_progress(done, total)`` of
    :func:`build_detail`), then the bodies built (``"bodies 1200/3400"``), the objects tessellated
    into the GLB (``"tessellating 1500/3800"``), ``"writing GLB"`` and ``"stats"`` (:meth:`stage`, the
    ``on_stage``), and the job's own uploads (:meth:`upload`). Each phase moves through its share of
    the fraction (:data:`DETAIL_SHARES`). At most one report per ``min_interval_s`` -- a step can take
    milliseconds and every report is a queue write -- but the first and the last of each phase are
    always reported, so a phase that starts is seen to start."""

    def __init__(
        self,
        report: Callable[[str, float], None],
        *,
        shares: Mapping[str, tuple[float, float]] | None = None,
        min_interval_s: float = 1.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.report = report
        self.shares = dict(DETAIL_SHARES if shares is None else shares)
        self.min_interval_s = min_interval_s
        self.clock = clock
        self._last: float | None = None
        self._phase: str | None = None
        self._frac = 0.0

    def __call__(self, done: int, total: int) -> None:
        self.stage("detail", done, total)

    def stage(self, name: str, done: int = 0, total: int = 0) -> None:
        now = self.clock()
        first = name != self._phase
        last = total == 0 or done >= total
        if not (first or last) and self._last is not None and now - self._last < self.min_interval_s:
            return
        self._phase, self._last = name, now
        if name in self.shares:
            start, end = self.shares[name]
            self._frac = start + (end - start) * (done / total if total else 1.0)
        self.report(f"{name} {done}/{total}" if total else name, round(self._frac, 4))

    def upload(self, what: str, size_bytes: int | None = None, *, index: int = 0, count: int = 1) -> None:
        """``"uploading GLB (12.3 MB)"``: the job's ``index``-th of ``count`` uploads, within
        :data:`UPLOAD_SHARE`."""
        label, frac = upload_stage(what, size_bytes, index=index, count=count)
        self._phase, self._last = label, self.clock()
        self.report(label, frac)


def upload_stage(what: str, size_bytes: int | None = None, *, index: int = 0, count: int = 1) -> tuple[str, float]:
    """The stage and fraction a detail job reports as it uploads ``what`` (its ``index``-th of
    ``count`` uploads): ``("uploading GLB (12.3 MB)", 0.95)``. For a job wrapper that reports
    through its queue rather than a :class:`DetailReporter`."""
    start, end = UPLOAD_SHARE
    label = f"uploading {what}" + (f" ({size_bytes / 1e6:.1f} MB)" if size_bytes is not None else "")
    return label, round(start + (end - start) * (index / max(count, 1)), 4)


def detail_progress(report: Callable[[str, float], None], **kw: Any) -> DetailReporter:
    """A :class:`DetailReporter` for ``report``: pass it as :func:`build_detail`'s ``on_progress``
    (its phases after the joints are reported through it too)."""
    return DetailReporter(report, **kw)


def build_detail(
    registered,
    *,
    spec_name: str,
    joint_ids: list[str],
    by_id: Mapping[str, Any],
    gen_options: Mapping[str, Any],
    on_progress: DetailProgress | None = None,
    on_stage: DetailStage | None = None,
    ck_of: Callable[[Mapping[str, Any]], "str | None"] | None = None,
    keep_built: list | None = None,
) -> tuple[bytes, dict]:
    """Build every named joint with ``registered`` and return ``(glb_bytes, stats)``.

    Every id must be in ``by_id`` -- the caller has already refused the ones that were not
    reproducible, with the message only it can word. Raises ``ValueError`` when nothing at all was
    built; a joint the builder refuses is reported and the rest go on.

    ``on_progress(done, total)`` is called before the first joint and after each one
    (:func:`stage_progress` turns it into a job's stage and fraction). ``on_stage(name, done,
    total)`` is told the phases after the joints: ``bodies`` (per chunk of :data:`BODY_CHUNK`
    bodies built), ``tessellating`` (per object of the GLB), ``writing GLB`` and ``stats``. Given a
    :class:`DetailReporter` (:func:`detail_progress`) as ``on_progress`` and no ``on_stage``, its
    :meth:`~DetailReporter.stage` is used, so one object reports every phase.

    WHAT DID NOT SURVIVE, in ``stats``:

    - ``skipped``: ``"<joint id>: <sentence>"`` per joint not built (kept for older readers).
    - ``not_built``: the same joints as records, ``{"joint_id", "kind", "reason", "declined"}``.
      ``kind`` is ``"declined"`` when every spec tried said "not mine" (``NotApplicable``; ``reason``
      is then the requested spec's), ``"failed"`` when a builder raised (``reason`` is its error,
      ``spec`` the spec that raised). ``declined`` lists ``{"spec", "reason"}`` per spec that declined.
    - ``weld_failures``: ``{"joint_id", "connection", "place", "weld_type", "reason"}`` per seam of a
      BUILT joint that is not in the model. Two sources: the builder's own account -- a builder that
      could not build a seam puts it in ``conn.metadata["weld_failures"]``
      (:data:`ada.api.connections.spec.WELD_FAILURES_KEY`: a list of ``{"place", "weld_type",
      "reason"}``) -- and the GLB export, for a weld the connection lists whose solid produced no
      geometry (``reason`` ``"no geometry: ..."``).
    - ``duplicate_welds``: ``{"joint_id", "connection", "weld", "weld_type", "kept_joint_id",
      "kept_connection", "kept_weld", "reason"}`` per weld taken out because another joint's weld is on
      the same seam (:func:`dedup_seams`, which also says which one stays). The seam is in the model,
      once; the joint's take-off counts the welds it kept.

    WHAT WAS BUILT, per connection, in ``stats["connections"]`` (:func:`connection_records`): the
    joint id it was built for, its name, spec, member roles and centre, its plates, and each weld with
    its type, throat, sides, length, members and the weld's own scalar metadata -- what the take-off's
    counts summarise, for a reader that has to say which seam is where. ``ck_of(record)`` gives each
    record its connection key (``ada.assoc``), where the caller can say which elements it joins.

    ``keep_built``, a list, is given every ``(joint id, connection)`` built -- for a caller that makes
    more of the objects than the model (a simulation subject per connection).
    """
    from ada import Part
    from ada.core.file_system import new_temp_path
    from ada.topo_model.takeoff import _joints_takeoff
    from ada.visit.tessellate import (
        collect_tessellation_failures,
        report_tessellation_progress,
    )

    joints_part = Part("Joints")
    skipped: list[str] = []
    not_built: list[dict[str, Any]] = []
    built: list[tuple[str, Any]] = []
    built_by: dict[str, int] = {}
    total = len(joint_ids)
    if on_progress is not None:
        on_progress(0, total)
    for done, jid in enumerate(joint_ids, start=1):
        found = by_id[jid]
        # The requested spec first; when its builder DECLINES (`NotApplicable`: "not mine"), the
        # joint's other applicable specs from the same provider, by priority -- the fall-through a
        # provider's own design loop does. Without it the highest-priority spec, which is the one
        # the panel batches a joint under, refused every joint it did not specialise in and the
        # run built nothing (a stub spec declining every plain joint, its plain sibling never tried).
        declined: list[dict[str, str]] = []
        for reg in [registered, *_fallbacks(registered, found)]:
            name = reg.spec.name
            outcome = _build_with(reg, name, jid, found, gen_options)
            if isinstance(outcome, _Declined):
                declined.append({"spec": name, "reason": outcome.reason})
                continue
            if isinstance(outcome, str):
                # A real failure (not a decline) of THIS spec: reported, and not passed on -- a
                # builder that crashed is not saying the joint belongs to another spec.
                skipped.append(f"{jid}: {outcome}")
                not_built.append(
                    {"joint_id": jid, "kind": "failed", "spec": name, "reason": outcome, "declined": declined}
                )
                break
            for conn in outcome:
                joints_part.add_part(conn)
                built.append((jid, conn))
            built_by[name] = built_by.get(name, 0) + 1
            break
        else:
            skipped.append(
                f"{jid}: no applicable spec took it ("
                + "; ".join(f"{d['spec']}: {d['reason']}" for d in declined)
                + ")"
            )
            not_built.append(
                {
                    "joint_id": jid,
                    "kind": "declined",
                    "reason": declined[0]["reason"] if declined else "no applicable spec took it",
                    "declined": declined,
                }
            )
        if on_progress is not None:
            on_progress(done, total)
    if not joints_part.parts and skipped:
        raise ValueError(
            f"{spec_name} detailed none of the {len(joint_ids)} joint(s) handed to it: " + "; ".join(skipped[:5])
        )

    # One weld per seam: where two joints welded the same physical seam (two members ending side by side
    # on one landing, a foot edge flush with the end of the member beside it), the second is taken out
    # of the model and said so (`duplicate_welds`), before anything is built or meshed.
    duplicate_welds = dedup_seams(built, by_id)

    if on_stage is None:
        on_stage = getattr(on_progress, "stage", None)

    def _phase(name: str, done: int = 0, total: int = 0) -> None:
        if on_stage is not None:
            on_stage(name, done, total)

    def _tessellated(done: int, total: int) -> None:
        _phase("tessellating", done, total)
        if done >= total:
            # The objects are meshed; what is left of to_gltf is the scene's export to bytes.
            _phase("writing GLB")

    _prebuild_bodies(joints_part, on_chunk=lambda done, total: _phase("bodies", done, total))
    glb_path = new_temp_path(suffix=".glb")
    try:
        with (
            collect_tessellation_failures() as tess_failures,
            report_tessellation_progress(_tessellated if on_stage is not None else None),
        ):
            joints_part.to_gltf(glb_path)
        glb_bytes = glb_path.read_bytes()
    finally:
        glb_path.unlink(missing_ok=True)

    _phase("stats")
    weld_failures = [*_reported_weld_failures(built), *_weld_geometry_failures(built, tess_failures)]

    stats: dict[str, Any] = {"joints": _joints_takeoff(joints_part)}
    # Which spec built how many joints: with fall-through, not every joint handed to `spec_name`
    # was built by it, and a take-off naming only the requested spec would misreport the rest.
    stats["built_by"] = built_by
    if skipped:
        stats["skipped"] = skipped
    stats["not_built"] = not_built
    stats["weld_failures"] = weld_failures
    stats["duplicate_welds"] = duplicate_welds
    stats["connections"] = connection_records(built, ck_of=ck_of)
    if keep_built is not None:
        keep_built.extend(built)
    return glb_bytes, stats


# ── one weld per seam ─────────────────────────────────────────────────────────────────────────

#: How far a point of one weld's line may be from another's and still be on the same seam [m]. Two
#: joints welding one edge put their lines on that edge; two seams of a real joint lie a wall or a
#: plate's thickness apart (a fillet each side of a web), well over this.
SEAM_TOL_M = 0.002

#: The share of a weld's line that must lie on a kept weld's line for the weld to be the same seam.
SEAM_COVER = 0.8

#: Points sampled along a weld's line to compare it with another.
_SEAM_SAMPLES = 11


def _weld_line(weld) -> Any:
    """A weld's line as an ``(n, 3)`` array: ``p1`` to ``p2``, or its sweep curve's points. None when
    the weld says neither -- such a weld is never compared, so never dropped."""
    import numpy as np

    p1, p2 = _point(getattr(weld, "p1", None)), _point(getattr(weld, "p2", None))
    if p1 is not None and p2 is not None:
        pts = [p1, p2]
    else:
        curve = getattr(weld, "sweep_curve", None)
        pts = []
        for attr in ("points3d", "points"):
            pts = [q for q in (_point(p) for p in as_items(getattr(curve, attr, None))) if q is not None]
            if len(pts) >= 2:
                break
    if len(pts) < 2:
        return None
    arr = np.asarray(pts, dtype=float)
    return arr if float(np.linalg.norm(np.diff(arr, axis=0), axis=1).sum()) > 0.0 else None


def _sample_line(line, n: int = _SEAM_SAMPLES):
    """``n`` points evenly spaced along the polyline ``line`` (ends included)."""
    import numpy as np

    seg = np.linalg.norm(np.diff(line, axis=0), axis=1)
    cum = np.concatenate([[0.0], np.cumsum(seg)])
    at = np.linspace(0.0, cum[-1], n)
    idx = np.clip(np.searchsorted(cum, at, side="right") - 1, 0, len(seg) - 1)
    frac = np.where(seg[idx] > 0, (at - cum[idx]) / np.where(seg[idx] > 0, seg[idx], 1.0), 0.0)
    return line[idx] + (line[idx + 1] - line[idx]) * frac[:, None]


def _distance_to_line(points, line):
    """Each of ``points``' distance to the polyline ``line``."""
    import numpy as np

    a, b = line[:-1], line[1:]
    ab = b - a
    denom = np.einsum("ij,ij->i", ab, ab)
    rel = points[:, None, :] - a[None, :, :]
    t = np.clip(np.einsum("pij,ij->pi", rel, ab) / np.where(denom > 0, denom, 1.0), 0.0, 1.0)
    nearest = a[None, :, :] + t[..., None] * ab[None, :, :]
    return np.linalg.norm(points[:, None, :] - nearest, axis=2).min(axis=1)


def _patch_outlines(found) -> list[Any]:
    """The outlines of the joint's contact patches with area (``contact.extras["patches"]``), each closed
    -- what tells whether a seam is on the contact this joint was found for."""
    import numpy as np

    extras = getattr(getattr(found, "contact", None), "extras", None) or {}
    out = []
    for patch in as_items(extras.get("patches")):
        if not isinstance(patch, Mapping):
            continue
        pts = [q for q in (_point(p) for p in as_items(patch.get("points_incoming"))) if q is not None]
        if len(pts) >= 3:
            arr = np.asarray(pts, dtype=float)
            out.append(np.vstack([arr, arr[:1]]))
    return out


def dedup_seams(built: list[tuple[str, Any]], by_id: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    """Take out of the model every weld that lies on a seam another joint's weld already covers, and
    return a record of each: ``{"joint_id", "connection", "weld", "weld_type", "kept_joint_id",
    "kept_connection", "kept_weld", "reason"}`` -- the take-off's ``duplicate_welds``.

    Two joints can weld one seam: two members ending side by side on one landing both weld the line
    between their footprints; a post's foot edge flush with the end of the member beside it is the end
    edge of each. Each builder sees only its own pair, so each welds the seam, typed against its own
    members. Seams are compared by their LINES -- every weld's sampled line within :data:`SEAM_TOL_M`
    of the other's over :data:`SEAM_COVER` of its length -- never by their profiles, which differ.
    The welds of one joint are never compared with each other.

    WHICH WELD STAYS, deterministically: the welds are visited in the order (1) a weld whose line lies on
    its own joint's contact patch outline (``by_id[jid].contact.extras["patches"]``) before one that
    does not -- the joint found AT that seam -- (2) the longer weld first, so a seam stays covered
    end to end, (3) the joint id, (4) the weld's place in its connection. A weld is kept unless a weld
    kept before it, of another joint, covers it; it is then removed from its connection (and from the
    connection's ``weld_names``) and recorded. A weld that partly overlaps a kept one is kept whole."""
    import numpy as np

    by_id = by_id or {}
    outlines = {jid: _patch_outlines(by_id.get(jid)) for jid in {j for j, _ in built}}
    entries = []
    for order, (jid, conn) in enumerate(built):
        for k, weld in enumerate(_welds_of(conn)):
            line = _weld_line(weld)
            if line is None:
                continue
            samples = _sample_line(line)
            on_patch = any(bool(np.all(_distance_to_line(samples, outline) <= SEAM_TOL_M)) for outline in outlines[jid])
            length = float(np.linalg.norm(np.diff(line, axis=0), axis=1).sum())
            entries.append(((not on_patch, -round(length, 4), jid, order, k), jid, conn, weld, line, samples))
    entries.sort(key=lambda e: e[0])

    cell = SEAM_TOL_M * 50  # a 0.1 m grid: a kept weld is looked up only where its line passes
    grid: dict[tuple[int, int, int], list[int]] = {}
    kept: list[tuple[str, Any, Any, Any]] = []
    dropped: list[dict[str, Any]] = []
    offsets = [(dx, dy, dz) for dx in (-1, 0, 1) for dy in (-1, 0, 1) for dz in (-1, 0, 1)]
    for _, jid, conn, weld, line, samples in entries:
        near: set[int] = set()
        for p in samples:
            cx, cy, cz = (int(c) for c in np.floor(p / cell))
            for dx, dy, dz in offsets:
                near.update(grid.get((cx + dx, cy + dy, cz + dz), ()))
        cover = None
        for idx in sorted(near):
            kjid, kconn, kweld, kline = kept[idx]
            if kjid == jid:
                continue
            if float(np.mean(_distance_to_line(samples, kline) <= SEAM_TOL_M)) >= SEAM_COVER:
                cover = kept[idx]
                break
        if cover is None:
            kept.append((jid, conn, weld, line))
            length = float(np.linalg.norm(np.diff(line, axis=0), axis=1).sum())
            for p in _sample_line(line, max(2, int(np.ceil(2 * length / cell)) + 1)):
                key = tuple(int(c) for c in np.floor(p / cell))
                if not grid.get(key) or grid[key][-1] != len(kept) - 1:
                    grid.setdefault(key, []).append(len(kept) - 1)
            continue
        kjid, kconn, kweld, _ = cover
        _remove_weld(conn, weld)
        weld_type = getattr(getattr(weld, "type", None), "value", None) or getattr(weld, "type", None)
        dropped.append(
            {
                "joint_id": jid,
                "connection": str(getattr(conn, "name", "") or ""),
                "weld": str(getattr(weld, "name", "") or ""),
                "weld_type": str(weld_type or ""),
                "kept_joint_id": kjid,
                "kept_connection": str(getattr(kconn, "name", "") or ""),
                "kept_weld": str(getattr(kweld, "name", "") or ""),
                "reason": f"the same seam as {getattr(kweld, 'name', '')} of {getattr(kconn, 'name', '')}, kept",
            }
        )
    return dropped


def _remove_weld(conn, weld) -> None:
    """``weld`` out of the part that holds it (``conn`` or a sub-part of it) and out of ``conn``'s
    ``connection_info["weld_names"]``, so the take-off and the GLB both leave it out."""
    holder = getattr(weld, "parent", None)
    for part in (holder, conn):
        welds = getattr(part, "_welds", None)
        if isinstance(welds, list) and any(w is weld for w in welds):
            welds[:] = [w for w in welds if w is not weld]
            invalidate = getattr(part, "_invalidate_welds_for_cache", None)
            if callable(invalidate):
                invalidate()
            break
    metadata = getattr(conn, "metadata", None)
    info = metadata.get("connection_info") if isinstance(metadata, Mapping) else None
    if isinstance(info, dict) and isinstance(info.get("weld_names"), list):
        info["weld_names"] = [n for n in info["weld_names"] if n != getattr(weld, "name", None)]


def _scalar(value: Any) -> Any:
    """``value`` when JSON writes it as itself (str, bool, int, finite float), else None."""
    if isinstance(value, bool) or isinstance(value, (str, int)):
        return value
    if isinstance(value, float):
        return value if value == value and abs(value) != float("inf") else None
    return None


def _point(p: Any) -> tuple[float, float, float] | None:
    p = getattr(p, "p", p)
    try:
        x, y, z = (float(c) for c in p)
    except (TypeError, ValueError):
        return None
    return (x, y, z)


def _polyline_length(points: Any) -> float | None:
    pts = [q for q in (_point(p) for p in as_items(points)) if q is not None]
    if len(pts) < 2:
        return None
    return sum(sum((b[i] - a[i]) ** 2 for i in range(3)) ** 0.5 for a, b in zip(pts, pts[1:]))


def _weld_length(weld) -> float | None:
    """The weld line's length: ``p1`` to ``p2``, or along its sweep curve's points. None when the
    weld says neither."""
    p1, p2 = _point(getattr(weld, "p1", None)), _point(getattr(weld, "p2", None))
    if p1 is not None and p2 is not None:
        return _polyline_length([p1, p2])
    curve = getattr(weld, "sweep_curve", None)
    if curve is None:
        return None
    for attr in ("points3d", "points"):
        length = _polyline_length(getattr(curve, attr, None))
        if length is not None:
            return length
    return None


def _weld_record(weld) -> dict[str, Any]:
    metadata = getattr(weld, "metadata", None)
    weld_type = getattr(getattr(weld, "type", None), "value", None) or getattr(weld, "type", None)
    length = _weld_length(weld)
    throat = getattr(weld, "throat", None)
    return {
        "name": str(getattr(weld, "name", "") or ""),
        "weld_type": str(weld_type or ""),
        "throat": _scalar(float(throat)) if isinstance(throat, (int, float)) else None,
        "sided": str(getattr(weld, "sided", "") or ""),
        "length": round(length, 6) if length is not None else None,
        "members": [str(getattr(m, "name", m)) for m in (getattr(weld, "members", None) or ())],
        # A builder's own words for the seam (where it is, which side, which corner), where it wrote any.
        "metadata": (
            {str(k): v for k, v in ((k, _scalar(v)) for k, v in metadata.items()) if v is not None}
            if isinstance(metadata, Mapping)
            else {}
        ),
    }


def connection_records(
    built: list[tuple[str, Any]], *, ck_of: Callable[[Mapping[str, Any]], "str | None"] | None = None
) -> list[dict[str, Any]]:
    """One record per built connection: ``{"joint_id", "connection", "spec", "member_roles",
    "centre", "plates", "welds"}``, each weld as :func:`_weld_record` reads it, and its ``ck``
    (``ada.assoc.keys.connection_key``) when ``ck_of`` gives one. Read defensively: a provider's
    builder made these objects."""
    out: list[dict[str, Any]] = []
    for jid, conn in built:
        metadata = getattr(conn, "metadata", None)
        info = (metadata.get("connection_info") if isinstance(metadata, Mapping) else None) or {}
        roles = info.get("member_roles") if isinstance(info.get("member_roles"), Mapping) else {}
        centre = info.get("centre")
        out.append(
            {
                "joint_id": jid,
                "connection": str(getattr(conn, "name", "") or ""),
                "spec": str(info.get("spec_name") or getattr(conn, "spec_name", "") or ""),
                "member_roles": {str(r): [str(n) for n in (names or [])] for r, names in roles.items()},
                "centre": list(centre) if isinstance(centre, (list, tuple)) else None,
                "plates": [str(getattr(p, "name", p)) for p in (getattr(conn, "plates", None) or ())],
                "welds": [_weld_record(w) for w in _welds_of(conn)],
            }
        )
        if ck_of is not None:
            try:
                ck = ck_of(out[-1])
            except Exception:  # noqa: BLE001 - a connection whose elements are not known has no ck
                ck = None
            if ck:
                out[-1]["ck"] = ck
    return out


#: Bodies built per :func:`ada.cad.shape_cache.prebuild_solids` call: big enough that each call's
#: parallel batch keeps every core busy, small enough that a run of thousands reports as it goes.
BODY_CHUNK = 256


def _prebuild_bodies(part, on_chunk: Callable[[int, int], None] | None = None) -> None:
    """Build the bodies of everything the joints made in batches of :data:`BODY_CHUNK`, before the
    GLB writer asks for them one by one: faceted ones (weld beads) together and in parallel where
    the CAD backend can (:func:`ada.cad.shape_cache.prebuild_solids`). Bodies a builder already built
    (one that checks its welds' solids as it goes) are cached and skipped. A body that fails here is
    left to the writer, which reports it as a weld without geometry -- so nothing is lost or said
    twice. ``on_chunk(done, total)`` before the first batch and after each."""
    # The objects the GLB writer tessellates (`tessellate_part`): the physical ones and the welds.
    objects = [
        o
        for o in (*part.get_all_physical_objects(pipe_to_segments=True), *part.get_all_welds())
        if hasattr(o, "solid_geom")
    ]
    if on_chunk is not None:
        on_chunk(0, len(objects))
    if not objects:
        return
    from ada.cad.shape_cache import prebuild_solids

    for start in range(0, len(objects), BODY_CHUNK):
        prebuild_solids(objects[start : start + BODY_CHUNK])
        if on_chunk is not None:
            on_chunk(min(start + BODY_CHUNK, len(objects)), len(objects))


def _reported_weld_failures(built: list[tuple[str, Any]]) -> list[dict[str, str]]:
    """The seams each built connection's builder says it could not build
    (``metadata["weld_failures"]``). Read defensively: it is a provider's document."""
    from ada.api.connections.spec import WELD_FAILURES_KEY

    out: list[dict[str, str]] = []
    for jid, conn in built:
        metadata = getattr(conn, "metadata", None)
        entries = metadata.get(WELD_FAILURES_KEY) if isinstance(metadata, Mapping) else None
        if not isinstance(entries, (list, tuple)):
            continue
        for entry in entries:
            if not isinstance(entry, Mapping):
                continue
            out.append(
                {
                    "joint_id": jid,
                    "connection": str(getattr(conn, "name", "") or ""),
                    "place": str(entry.get("place") or ""),
                    "weld_type": str(entry.get("weld_type") or ""),
                    "reason": str(entry.get("reason") or ""),
                }
            )
    return out


def _weld_geometry_failures(built: list[tuple[str, Any]], tess_failures) -> list[dict[str, str]]:
    """The welds a built connection lists that are not in the GLB: the export skipped the ones whose
    solid could not be built or meshed (``tess_failures``), and a name in ``connection_info`` that
    is no weld of the connection has nothing to draw at all."""
    failed_by_guid = {f.guid: f for f in tess_failures if f.guid}
    failed_by_name = {f.name: f for f in tess_failures if f.name}
    out: list[dict[str, str]] = []
    for jid, conn in built:
        welds = list(_welds_of(conn))
        info = (getattr(conn, "metadata", None) or {}).get("connection_info") or {}
        listed = [str(n) for n in (info.get("weld_names") or []) if n]
        conn_name = str(getattr(conn, "name", "") or "")
        for weld in welds:
            failure = failed_by_guid.get(getattr(weld, "guid", None)) or failed_by_name.get(getattr(weld, "name", None))
            if failure is None:
                continue
            weld_type = getattr(getattr(weld, "type", None), "value", None) or ""
            out.append(
                {
                    "joint_id": jid,
                    "connection": conn_name,
                    "place": str(weld.name or ""),
                    "weld_type": str(weld_type),
                    "reason": f"no geometry: {failure.error_type}: {failure.error}",
                }
            )
        names = {w.name for w in welds}
        for name in listed:
            if name not in names:
                out.append(
                    {
                        "joint_id": jid,
                        "connection": conn_name,
                        "place": name,
                        "weld_type": "",
                        "reason": "no geometry: listed by the connection but not one of its welds",
                    }
                )
    return out


def _welds_of(conn):
    """Every weld under ``conn`` (a builder may keep its welds in a sub-part)."""
    get_all = getattr(conn, "get_all_welds", None)
    if callable(get_all):
        return get_all()
    return list(getattr(conn, "welds", None) or [])


class JointNotBuilt(ValueError):
    """:func:`build_joint` built nothing: every spec declined (``declined`` lists them), or a builder
    failed (``failed``: its spec and error)."""

    def __init__(self, message: str, *, declined: list | None = None, failed: dict | None = None) -> None:
        super().__init__(message)
        self.declined = list(declined or [])
        self.failed = failed


def build_joint(registered, found, *, joint_id: str, gen_options: Mapping[str, Any] | None = None) -> tuple[str, list]:
    """Build ONE joint as :func:`build_detail` builds each of its joints: the requested spec first,
    then the same provider's other applicable specs when it declines. Returns ``(the name of the
    spec that built it, its connections)``; :class:`JointNotBuilt` when none did.

    The connections are named as a detail run names them (``<spec>_<joint id>``, ``_<i>`` after the
    first), so the same joint built again carries the same names -- how a subject is made of the
    connection a detail model listed, without the model."""
    declined: list[dict[str, str]] = []
    for reg in [registered, *_fallbacks(registered, found)]:
        name = reg.spec.name
        outcome = _build_with(reg, name, joint_id, found, dict(gen_options or {}))
        if isinstance(outcome, _Declined):
            declined.append({"spec": name, "reason": outcome.reason})
            continue
        if isinstance(outcome, str):
            raise JointNotBuilt(f"{joint_id}: {outcome}", declined=declined, failed={"spec": name, "reason": outcome})
        return name, outcome
    raise JointNotBuilt(
        f"{joint_id}: no applicable spec took it (" + "; ".join(f"{d['spec']}: {d['reason']}" for d in declined) + ")",
        declined=declined,
    )


class _Declined:
    """A builder's `NotApplicable`: the joint is not this spec's, try the next one."""

    def __init__(self, reason: str) -> None:
        self.reason = reason


def _is_decline(exc: BaseException) -> bool:
    """Core's `NotApplicable`, or a provider's own exception of that NAME on an older core."""
    from ada.api.connections.spec import NotApplicable

    return isinstance(exc, NotApplicable) or any(c.__name__ == "NotApplicable" for c in type(exc).__mro__)


def _build_with(reg, spec_name: str, jid: str, found, gen_options) -> "list[Any] | _Declined | str":
    """Every connection ``reg`` builds at this joint, a decline, or a failure sentence."""
    from ada.clash.match import detail_pairs

    # One connection per way the spec's roles bind at this contact -- a joint is a contact NODE and
    # three or four members can meet at one, while a builder takes a pair. See
    # `ada.clash.match.detail_pairs`: requiring the joint itself to have exactly two members refused
    # every column head, which is the first thing a "detail everything" run hits.
    try:
        pairs = detail_pairs(reg.spec, found)
    except ValueError as exc:
        # The spec's roles do not bind here: as good as a decline for falling through.
        return _Declined(str(exc))
    built = []
    for i, (landing, incoming) in enumerate(pairs):
        try:
            conn = reg.fn(
                landing=landing,
                incoming=incoming,
                centre=found.centre,
                # What the PASS measured at this contact, where it measured anything: normal,
                # penetration depth, nearest points, patch area. A pass that works on axes has none
                # and this is None, which every builder tolerates -- but a builder given a geometric
                # contact can size its output from the real overlap instead of inferring one.
                clash=found.contact,
                name=f"{spec_name}_{jid}" if i == 0 else f"{spec_name}_{jid}_{i}",
                **gen_options,
            )
        except Exception as exc:  # noqa: BLE001 - one joint's refusal is not the run's
            if _is_decline(exc):
                return _Declined(str(exc))
            # A builder's own prerequisites can be finer than a spec's criteria express; the other
            # joints in the batch are still worth building. Only a run where NOTHING built fails.
            return f"{type(exc).__name__}: {exc}"
        _record_connection(conn, reg.spec.name, landing=landing, incoming=incoming, centre=found.centre)
        built.append(conn)
    return built


def _record_connection(conn, spec_name: str, *, landing, incoming, centre) -> None:
    """Give a builder's connection the ``connection_info`` record the take-off reads, when the
    builder wrote none. The built-in specs write their own; a provider's builder is not asked to, and
    without it the take-off listed its joint with no members, no plates and no welds."""
    from ada.api.connections.joints import Connection

    if not isinstance(conn, Connection):
        return
    metadata = conn.metadata
    if metadata.get("connection_info"):
        return
    metadata["connection_info"] = {
        "name": conn.name,
        "spec_name": getattr(conn, "spec_name", None) or spec_name,
        "member_roles": {"landing": [landing.name], "incoming": [incoming.name]},
        "plate_names": [p.name for p in conn.plates],
        "weld_names": [w.name for w in conn.welds],
        "centre": [round(float(v), 4) for v in centre] if centre is not None else None,
    }


def _fallbacks(registered, found) -> list:
    """The joint's OTHER applicable specs from the same provider (the same capability), highest
    priority first -- what a decline falls through to.

    Same provider only: a provider's specs are written to fall through to one another, while a
    different provider's spec (a built-in among them) is a different detailing decision, made by
    choosing that spec, not by one provider's builder saying "not mine"."""
    from ada.api.connections.spec import all_registered
    from ada.clash.match import detail_pairs

    cap = getattr(registered.spec, "capability", None)
    out = []
    for reg in all_registered():
        if reg.spec.name == registered.spec.name or getattr(reg.spec, "capability", None) != cap:
            continue
        # Qualified exactly as the requested spec is -- by whether its roles bind at this contact
        # (`detail_pairs`) -- so a fall-through candidate is one the build would accept.
        try:
            if detail_pairs(reg.spec, found):
                out.append(reg)
        except ValueError:
            continue
    out.sort(key=lambda r: (-(r.spec.priority or 0), r.spec.name))
    return out
