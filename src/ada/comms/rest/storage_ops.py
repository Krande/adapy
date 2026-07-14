"""Shared storage mutation helpers with derived-blob cascade.

Delete / rename / move-to-folder all have to drag the derived-blob zoo
(`_derived/<src>.*` convert outputs, `.fea/` artefact trees, meta caches,
profile dumps) along with their source so the bake cache survives and no
orphans are left behind. The logic originated in the admin-only endpoints;
it lives here so the user-level (personal-scope) endpoints share one
implementation. Handlers own HTTP-body validation and audit logging —
helpers take parsed inputs and return plain dicts, raising HTTPException
only where the admin handlers historically did (empty key, source-delete
failure).
"""

from __future__ import annotations

import re

from fastapi import HTTPException

from ada.config import logger

from .converter import TARGET_FORMATS, is_derived_key
from .scope import Scope
from .storage import Storage


def derived_source_of(derived_key: str) -> tuple[str, str] | None:
    """Recover (source_key, target_label) from a derived key.

    Handles the full derived-key zoo so storage listings attribute every
    derived artefact to its real source instead of dropping it (silently)
    or surfacing a fake source as an orphan:

    * ``<src>.fea/<file>`` — streaming-viewer artefact tree
      (mesh GLB, manifest, mesh-edges sidecar, per-field blobs).
    * ``<src>.meta.json`` — legacy result-meta cache.
    * ``<src>.<job_id>.prof`` — per-job cProfile dump.
    * ``<src>.s<N>.<field>.<fmt>`` — legacy SIF step/field pick.
    * ``<src>.<fmt>`` — plain legacy convert output.
    """

    if not is_derived_key(derived_key):
        return None
    stripped = derived_key[len("_derived/") :]

    # Streaming-FEA artefact tree: `<src>.fea/<filename>`. The
    # ".fea/" infix is anchored to the source's extension; finding
    # it splits the key cleanly into source + artefact filename.
    fea_idx = stripped.find(".fea/")
    if fea_idx >= 0:
        src_key = stripped[:fea_idx]
        filename = stripped[fea_idx + len(".fea/") :]
        return src_key, f"fea/{filename}"

    # Legacy result-meta cache.
    if stripped.endswith(".meta.json"):
        return stripped[: -len(".meta.json")], "meta.json"

    # Per-job profile blob: `<src>.<job_id>.prof`. job_id is
    # uuid-hex (no dots) so the last dot before .prof bounds it.
    if stripped.endswith(".prof"):
        without_prof = stripped[: -len(".prof")]
        last_dot = without_prof.rfind(".")
        if last_dot > 0:
            return without_prof[:last_dot], "prof"

    # Legacy SIF step/field pick OR bare `<src>.<fmt>`. Try the
    # step/field shape first when the candidate source ends in
    # `.sif`; fall back to the bare match otherwise (avoids
    # mis-attributing a non-SIF source whose name happens to
    # contain `.s<digits>.`).
    for tgt in TARGET_FORMATS:
        suffix = "." + tgt
        if stripped.endswith(suffix):
            without_tgt = stripped[: -len(suffix)]
            m = re.search(r"\.s\d+\.", without_tgt)
            if m:
                candidate_src = without_tgt[: m.start()]
                if candidate_src.lower().endswith(".sif"):
                    return candidate_src, tgt
            return without_tgt, tgt

    return None


def owning_source(derived_key: str, candidates: list[str]) -> str | None:
    """Return the longest source key whose derived prefix matches.

    Derived blobs follow `_derived/<src>.<suffix>` (legacy GLB,
    FEA artefacts, result-meta, etc.). When two source keys share
    a string prefix (e.g. ``wall.rmed`` and ``wall.rmed.bak``),
    naively matching the shorter prefix would steal the longer
    source's derived blobs on rename. Pick the longest source-key
    prefix followed by ``.`` or ``/`` and trust *that*.
    """
    if not derived_key.startswith("_derived/"):
        return None
    inner = derived_key[len("_derived/") :]
    best: str | None = None
    for src in candidates:
        if inner.startswith(src + ".") or inner.startswith(src + "/"):
            if best is None or len(src) > len(best):
                best = src
    return best


async def delete_blob_cascade(storage: Storage, scope_obj: Scope, key: str) -> dict:
    """Delete a blob; sources cascade to their derived siblings.

    Returns ``{"deleted": [...], "errors": [...]}``. Pointing at a derived
    blob deletes only that blob — except streaming-FEA artefacts, where the
    whole ``<src>.fea/`` tree is reaped as a unit (the manifest references
    every other file by name, so a partial delete leaves the picker
    rendering against a stale manifest pointing at missing data).
    """

    clean = key.lstrip("/")
    if not clean:
        raise HTTPException(status_code=400, detail="empty key")

    # If the caller pointed at a derived blob, only that blob goes;
    # don't fan out and delete the source under their feet.
    if is_derived_key(clean):
        parsed = derived_source_of(clean)
        if parsed is not None and parsed[1].startswith("fea/"):
            prefix = f"_derived/{parsed[0]}.fea/"
            scope_keys = [f.key for f in await storage.list(scope_obj)]
            tree_keys = [k for k in scope_keys if k.startswith(prefix)]
            deleted_tree: list[str] = []
            tree_errors: list[str] = []
            for k in tree_keys:
                try:
                    await storage.delete(scope_obj, k)
                    deleted_tree.append(k)
                except FileNotFoundError:
                    continue
                except Exception as exc:
                    msg = str(exc).lower()
                    if "not found" in msg or "no such" in msg:
                        continue
                    logger.warning(
                        "storage-op: failed to delete %s in fea-tree reap: %s",
                        k,
                        exc,
                    )
                    # Report only the failed key to the client; the exception detail is
                    # logged above (avoid leaking backend/stack-trace text in the response).
                    tree_errors.append(k)
            return {"deleted": deleted_tree, "errors": tree_errors}

        # Plain single-blob derived (legacy GLB, meta cache,
        # profile, etc.) — drop just the one file.
        try:
            await storage.delete(scope_obj, clean)
        except Exception as exc:
            logger.exception("storage-op: delete failed for %s", clean)
            raise HTTPException(status_code=500, detail="delete failed") from exc
        return {"deleted": [clean], "errors": []}

    # Source delete: also reap every derived blob keyed off this
    # source. List the scope and filter via derived_source_of so
    # the full derived zoo (FEA artefact tree, result-meta cache,
    # SIF step/field picks, profile blobs, plain converts) lands
    # in the candidate set.
    candidates = [clean]
    scope_keys = [f.key for f in await storage.list(scope_obj)]
    for k in scope_keys:
        if not is_derived_key(k):
            continue
        parsed = derived_source_of(k)
        if parsed is None:
            continue
        derived_src, _label = parsed
        if derived_src == clean:
            candidates.append(k)

    deleted: list[str] = []
    errors: list[str] = []
    for k in candidates:
        try:
            await storage.delete(scope_obj, k)
            deleted.append(k)
        except FileNotFoundError:
            # Derived blob just wasn't there — that's fine.
            continue
        except Exception as exc:
            # Some backends raise a generic error for "not found";
            # treat it as benign for derived siblings, but if the
            # source itself can't be deleted, surface the failure.
            msg = str(exc).lower()
            if "not found" in msg or "no such" in msg:
                continue
            if k == clean:
                logger.exception("storage-op: delete failed for source %s", clean)
                raise HTTPException(status_code=500, detail="delete failed") from exc
            # Report only the failed key to the client; log the exception detail server-side
            # (avoid leaking backend/stack-trace text in the response).
            logger.warning("storage-op: failed to delete derived %s: %s", k, exc)
            errors.append(k)

    return {"deleted": deleted, "errors": errors}


async def rename_key_cascade(
    storage: Storage,
    scope_obj: Scope,
    old_key: str,
    new_key: str,
    live_keys: set[str],
) -> dict:
    """Rename one source key, dragging its derived siblings along.

    ``live_keys`` is the caller's snapshot of the scope's keyset; it is
    mutated as renames happen so batch callers agree on what exists.
    Returns a moved entry ``{"old", "new", "siblings_moved",
    "siblings_failed"}`` on success or ``{"key", "reason"}`` on failure
    (the caller distinguishes via the presence of ``"reason"``).
    """

    if is_derived_key(old_key):
        return {"key": old_key, "reason": "cannot move derived blobs directly"}
    if new_key == old_key:
        return {"key": old_key, "reason": "destination matches source"}
    if new_key in live_keys:
        return {"key": old_key, "reason": f"target already exists: {new_key}"}
    if old_key not in live_keys:
        return {"key": old_key, "reason": "source not found"}

    # The collision guard is the `new_key in live_keys` pre-check above —
    # application layer. Pass overwrite=True because S3-compatible
    # backends raise ``copy-if-not-exists not supported`` for the safer
    # default. The narrow remaining race (two callers renaming into the
    # same key concurrently) would clobber, but the audit log records
    # every move.
    try:
        await storage.rename(scope_obj, old_key, new_key, overwrite=True)
    except Exception:
        # Full detail logged; return a generic reason so backend/stack-trace text isn't
        # exposed in the response (CodeQL py/stack-trace-exposure).
        logger.exception("storage-op: rename failed for %s -> %s", old_key, new_key)
        return {"key": old_key, "reason": "rename failed"}

    live_keys.discard(old_key)
    live_keys.add(new_key)

    # Derived siblings: keys under `_derived/<old_key>.*` get the prefix
    # swapped to `_derived/<new_key>.*` so the convert / bake cache stays
    # warm (re-baking a big SIF / RMED is expensive). Candidate set must
    # include old_key — it was just removed from live_keys by the source
    # rename above, but the derived blobs we're looking up still
    # reference its name.
    old_prefix = f"_derived/{old_key}"
    new_prefix = f"_derived/{new_key}"
    candidates = [k for k in live_keys if not is_derived_key(k)] + [old_key]
    sibling_pairs: list[tuple[str, str]] = []
    for k in list(live_keys):
        if not k.startswith(old_prefix):
            continue
        # Pin each derived blob to its longest matching source key —
        # without this, `wall.rmed.bak.glb` would be stolen as a
        # sibling of `wall.rmed`.
        if owning_source(k, candidates) != old_key:
            continue
        rest = k[len(old_prefix) :]
        sibling_pairs.append((k, new_prefix + rest))

    sibling_errors: list[str] = []
    for sk_old, sk_new in sibling_pairs:
        # overwrite=True for the same S3 reason as the source rename.
        # Sibling collisions are rare in practice because the
        # destination derived prefix is always new (we just renamed
        # the source to a new key).
        try:
            await storage.rename(scope_obj, sk_old, sk_new, overwrite=True)
            live_keys.discard(sk_old)
            live_keys.add(sk_new)
        except Exception as exc:
            logger.warning(
                "storage-op: sibling rename %s -> %s failed: %s",
                sk_old,
                sk_new,
                exc,
            )
            # Report only the failed key to the client; exception detail is logged above.
            sibling_errors.append(sk_old)

    return {
        "old": old_key,
        "new": new_key,
        "siblings_moved": len(sibling_pairs) - len(sibling_errors),
        "siblings_failed": sibling_errors,
    }


async def move_keys_to_folder(
    storage: Storage,
    scope_obj: Scope,
    raw_keys: list[str],
    folder: str,
) -> dict:
    """Batch-move source keys to a destination folder prefix.

    Each source key is renamed to ``<folder>/<basename(src_key)>``
    within the same scope. Per-key outcome reporting: failures (target
    exists, rename backend error, etc.) don't abort the batch — the
    caller gets ``{"moved", "failed"}`` and can re-attempt the failures.
    """

    # Dedup while preserving order.
    seen: set[str] = set()
    keys: list[str] = []
    for raw in raw_keys:
        cleaned = raw.strip().lstrip("/")
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            keys.append(cleaned)

    # Snapshot the scope's keyset so we can detect target collisions
    # and find derived siblings without re-listing per file. The set is
    # mutated as renames happen so multiple moves into the same folder
    # agree on what already exists.
    live_keys = {f.key for f in await storage.list(scope_obj)}

    moved: list[dict] = []
    failed: list[dict] = []
    for old_src in keys:
        basename = old_src.rsplit("/", 1)[-1]
        new_src = f"{folder}/{basename}"
        result = await rename_key_cascade(storage, scope_obj, old_src, new_src, live_keys)
        if "reason" in result:
            failed.append(result)
        else:
            moved.append(result)

    return {"moved": moved, "failed": failed}
