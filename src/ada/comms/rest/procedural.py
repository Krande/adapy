"""Procedural cell models: key conventions + document validation.

The document is the single source of truth for a viewer-built cell model:
``{"grid": {...}, "spaces": [TopoSpace...], "equipments": [TopoEquipment...],
"openings": [TopoOpening...]}``. Validation round-trips through the
``ada.topology.entities`` pydantic models (shape/type checking only — geometry
completeness is the compile worker's job).
"""

from __future__ import annotations

from functools import lru_cache

PROCEDURAL_PREFIX = "_procedural/"
# Hidden prefix for built external-engine wheels (see HIDDEN_PREFIXES).
ENGINE_PREFIX = "_engines/"
# How many compile-run logs to retain per procedural model (see procedural_run_dir).
RUN_LOG_RETENTION = 50


# Depth and length bounds on a model name-as-path. Not security — the name is
# never a filesystem path or a URL segment (models are addressed by UUID) — but
# a tree the browser has to render, and an unbounded one is a UI that hangs on
# somebody's paste accident.
MAX_MODEL_NAME_DEPTH = 12
MAX_MODEL_NAME_LEN = 512


def normalize_model_name(raw: str) -> str:
    """Normalise a procedural-model name that may carry a folder path.

    A model is a database row, not a blob, so it has no natural place in the
    storage tree — but the browser shows it beside real files and an operator
    reasonably wants it filed with them. Rather than inventing a second
    hierarchy (a ``folder`` column that only these rows have, which every
    listing and move would then have to special-case), the NAME carries the
    path and the existing tree builder does the rest.

    That is safe because a model is addressed by UUID everywhere: no route
    interpolates the name, so a ``/`` in it cannot escape anything. The unique
    index on (scope, name) keeps two models from claiming one path, which is
    exactly the constraint a filesystem would impose anyway.

    Normalises rather than merely rejects, because the shapes people type —
    a trailing slash, a doubled separator, backslashes pasted from Windows —
    all have one obvious intended meaning, and refusing them would be pedantry.
    What it does refuse is anything that would produce a tree node nobody can
    name or reach.
    """
    if not isinstance(raw, str):
        raise ValueError("name must be a string")
    # Windows-style separators are a paste artefact, never a deliberate choice.
    text = raw.replace("\\", "/").strip()
    segments = [seg.strip() for seg in text.split("/")]
    segments = [seg for seg in segments if seg]

    if not segments:
        raise ValueError("name is required")
    if any(seg in (".", "..") for seg in segments):
        # Not a traversal risk (this is not a path), but "../x" as a tree node
        # is a label that reads as navigation and is not.
        raise ValueError("name segments cannot be '.' or '..'")
    if len(segments) > MAX_MODEL_NAME_DEPTH:
        raise ValueError(f"name is nested deeper than {MAX_MODEL_NAME_DEPTH} folders")

    name = "/".join(segments)
    if len(name) > MAX_MODEL_NAME_LEN:
        raise ValueError(f"name is longer than {MAX_MODEL_NAME_LEN} characters")
    return name


def model_name_folder(name: str) -> str:
    """The folder part of a name-as-path, or "" at the root."""
    return name.rsplit("/", 1)[0] if "/" in name else ""


def engine_wheel_dir(engine_id: str) -> str:
    """Blob-key prefix under which a ``kind:wheel`` engine's built wheel lives."""
    return f"{ENGINE_PREFIX}{engine_id}/"


def engine_wheel_key(engine_id: str, filename: str) -> str:
    """Blob key for a built engine wheel (``_engines/{id}/{filename}``). The
    filename is pip's ``name-version-py3-none-any.whl`` — micropip parses it, so
    it must be preserved verbatim."""
    return f"{engine_wheel_dir(engine_id)}{filename}"


def procedural_source_key(model_id: str, ext: str = ".xlsx") -> str:
    """Blob key for a procedural model's ORIGINAL source document (e.g. the
    workbook it was imported from). Kept so a full-fidelity engine can
    compile the source directly (all config the topology doc drops); referenced by
    ``doc["source_xlsx_key"]``."""
    return f"{PROCEDURAL_PREFIX}{model_id}/source{ext.lower()}"


def _engine_suffix(engine: str | None) -> str:
    """Cache-key suffix distinguishing a non-default engine's output. The default
    engine keeps the bare key (backward compatible); any other engine's GLB is a
    separate blob so selecting it never serves the default's cached bytes."""
    if not engine or engine == "adapy-default":
        return ""
    # Keep the key filesystem/URL-safe (slugs are already, but be defensive).
    safe = "".join(c if (c.isalnum() or c in "-_") else "-" for c in engine)
    return f".{safe}"


def procedural_glb_key(model_id: str, revision: int, engine: str | None = None) -> str:
    """Blob key for a compiled model revision. Revision-stamped so the worker's
    cached-blob short-circuit makes recompiles of an unchanged revision free; the
    (non-default) ``engine`` gets its own key so engine outputs cache separately."""
    return f"{PROCEDURAL_PREFIX}{model_id}/r{revision}{_engine_suffix(engine)}.glb"


def procedural_detail_glb_key(model_id: str, revision: int, engine: str | None = None) -> str:
    """Blob key for the DETAIL level-of-detail compile of a model revision — a
    separate derived artifact from the simulation GLB (:func:`procedural_glb_key`),
    computed on demand when the user first opens the detail view. Revision-stamped
    the same way, so an unchanged revision's detail model is served from cache."""
    return f"{PROCEDURAL_PREFIX}{model_id}/r{revision}_detail{_engine_suffix(engine)}.glb"


def _detailing_suffix(detailing: str | None) -> str:
    """Cache-key fragment distinguishing a detailing-engine's output.

    CRITICAL backward-compat: ``None``/``"none"`` -> the EMPTY string, i.e. the
    plain structural key (a model with no detailing selected is byte-identical to
    today). ``"adapy-default"`` -> ``.det-adapy``; any other slug -> ``.det-<slug>``
    (kept filesystem/URL-safe)."""
    if not detailing or detailing == "none":
        return ""
    slug = "adapy" if detailing == "adapy-default" else detailing
    safe = "".join(c if (c.isalnum() or c in "-_") else "-" for c in slug)
    return f".det-{safe}"


def _detailing_options_fragment(detailing: str | None, detailing_options: dict | None) -> str:
    """Cache-key fragment distinguishing one set of per-joint detailing OPTIONS
    from another. Empty (no fragment) when no detailing is selected or the option
    map is empty, so the default (no-options) key is byte-identical to before this
    knob existed. Otherwise a short, stable hash of the normalized option map — a
    knob change (weld leg, plate thickness, overhang, clearance, a joint toggle)
    yields a DISTINCT key so the changed detailing never serves stale cached bytes."""
    if not detailing or detailing == "none" or not detailing_options:
        return ""
    import hashlib
    import json

    payload = json.dumps(detailing_options, sort_keys=True, separators=(",", ":"), default=str)
    return "-o" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:8]


def procedural_detailing_glb_key(
    model_id: str,
    revision: int,
    engine: str | None = None,
    detailing: str | None = None,
    lod: str = "sim",
    detailing_options: dict | None = None,
) -> str:
    """Blob key for a compiled model revision with a DETAILING engine applied.

    Composes with the existing suffixes so all four ``lod`` x ``detailing``
    combinations cache independently:
    ``_procedural/{id}/r{rev}{lod_suffix}{engine_suffix}{detailing_suffix}{options_fragment}.glb``.

    Because :func:`_detailing_suffix` returns ``""`` for ``None``/``"none"`` and
    :func:`_detailing_options_fragment` returns ``""`` for an empty option map, this
    reduces EXACTLY to :func:`procedural_glb_key` (sim) / :func:`procedural_detail_glb_key`
    (detail) when no detailing is selected — the plain structural key, byte-for-byte."""
    lod_suffix = "_detail" if lod == "detail" else ""
    return (
        f"{PROCEDURAL_PREFIX}{model_id}/r{revision}{lod_suffix}"
        f"{_engine_suffix(engine)}{_detailing_suffix(detailing)}"
        f"{_detailing_options_fragment(detailing, detailing_options)}.glb"
    )


def procedural_structural_ifc_key(model_id: str, revision: int, engine: str | None = None) -> str:
    """Blob key for the neutral STRUCTURAL artifact (IFC) an external (Tier-B)
    detailing engine consumes — the compiled ``ada.Part`` serialized so a foreign
    capability pool can deserialize it back to members with section tags. Written
    alongside the structural GLB (Phase 2 wiring)."""
    return f"{PROCEDURAL_PREFIX}{model_id}/r{revision}{_engine_suffix(engine)}.structural.ifc"


def procedural_structural_sections_key(model_id: str, revision: int, engine: str | None = None) -> str:
    """Blob key for the section-metadata SIDECAR that companions the neutral
    structural IFC artifact (:func:`procedural_structural_ifc_key`): the SAME base
    with ``.structural.ifc`` swapped for ``.structural.sections.json`` (one rule, so
    the sidecar key can never drift from the IFC key). Carries
    ``{member_name: {"section_type": beam.section.type.value, "section_props": {...}}}``
    for every Beam so an external (Tier-B) detailing engine can guarantee section-type
    (``BOX``/…) detection without re-deriving it from a lossy IFC round-trip."""
    ifc_key = procedural_structural_ifc_key(model_id, revision, engine)
    return ifc_key[: -len(".structural.ifc")] + ".structural.sections.json"


def doc_content_hash(doc: dict) -> str:
    """A short, stable content hash of a (normalized) procedural doc — the cache
    key for an ephemeral *preview* compile. Deterministic (sorted keys, compact
    separators) so the same doc always hashes the same, and so a preview keyed on
    ``validate_doc(doc)`` matches the hash recomputed at commit time (letting the
    commit promote the already-built preview blob instead of recompiling)."""
    import hashlib
    import json

    payload = json.dumps(doc, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def procedural_preview_glb_key(
    model_id: str,
    doc_hash: str,
    engine: str | None = None,
    lod: str = "sim",
    detailing: str | None = None,
    detailing_options: dict | None = None,
) -> str:
    """Blob key for an EPHEMERAL preview compile — a build of the current,
    *uncommitted* document so the user can visualize edits before deciding to
    commit. Keyed by the doc's content hash (not a revision), so re-previewing an
    unchanged doc is free and no revision is minted until the user commits. Lives
    under ``_procedural/{id}/preview/`` (hidden from listings, GC-able as a group).

    Gains the same ``.det-*`` fragment as the committed key so a detailing preview
    caches separately; ``detailing`` ``None``/``"none"`` keeps the bare key."""
    lod_suffix = "_detail" if lod == "detail" else ""
    return (
        f"{PROCEDURAL_PREFIX}{model_id}/preview/{doc_hash}{lod_suffix}"
        f"{_engine_suffix(engine)}{_detailing_suffix(detailing)}"
        f"{_detailing_options_fragment(detailing, detailing_options)}.glb"
    )


def procedural_log_key(glb_key: str) -> str:
    """LEGACY blob key for the engine-compile log: a ``.log`` sibling of the GLB
    derived key (the same path with ``.log`` swapped in for ``.glb``).

    Superseded by :func:`procedural_run_log_key` and kept only so artifacts built
    before compile runs existed still surface their log. Do NOT write here: the
    derived key is content-addressed, so every compile of the same input shares
    this one key — the reason a fresh run used to read back an older run's log."""
    if glb_key.endswith(".glb"):
        return f"{glb_key[: -len('.glb')]}.log"
    return f"{glb_key}.log"


# ── Compile RUNS ──────────────────────────────────────────────────────
#
# A compile LOG belongs to a RUN, not to a document. The derived-artifact keys
# above are deliberately CONTENT-ADDRESSED (a revision stamp, or the doc's
# content hash for a preview) so an unchanged input is served from cache for
# free — which means two different compile ATTEMPTS of the same input share one
# key. Deriving the log key from the artifact key therefore made a fresh run read
# back the PREVIOUS run's log: a forced recompile of an unchanged document,
# whose own log happened to be empty, still showed yesterday's failure.
#
# So the log gets its own identity: the queue job id, minted per compile attempt.
# It is already the id the compile response hands the viewer and the join key of
# the ``audit_log`` row, so one id names the log blob, the panel's current run and
# the admin audit entry.

RUN_ID_MAX_LEN = 64


def procedural_run_dir(model_id: str) -> str:
    """Blob-key prefix holding one model's compile-RUN logs. Lives under the
    model's hidden ``_procedural/`` prefix (never listed) and, like the preview
    prefix, is GC-able as a group — see :func:`prune_run_log_keys`."""
    return f"{PROCEDURAL_PREFIX}{model_id}/runs/"


def is_valid_run_id(run_id: str) -> bool:
    """Whether ``run_id`` is safe to interpolate into a blob key. Queue job ids are
    ``uuid4().hex``; the WASM path prefixes them. Anything outside
    ``[A-Za-z0-9_-]`` (notably ``/`` and ``.``) is rejected so a caller-supplied
    id can never escape :func:`procedural_run_dir`."""
    if not run_id or len(run_id) > RUN_ID_MAX_LEN:
        return False
    return all(c.isalnum() or c in "-_" for c in run_id)


def procedural_run_log_key(model_id: str, run_id: str) -> str:
    """Blob key for ONE compile run's log — ``_procedural/{id}/runs/{run}.log``.

    Keyed by the RUN, not by the artifact: every attempt writes its own blob, so a
    second compile of the very same document can never serve the first's log, and
    a failed run's log survives alongside the successful run that follows it.
    Raises on a run id that isn't key-safe (:func:`is_valid_run_id`)."""
    if not is_valid_run_id(run_id):
        raise ValueError(f"invalid compile run id: {run_id!r}")
    return f"{procedural_run_dir(model_id)}{run_id}.log"


def procedural_run_pointer_key(derived_key: str) -> str:
    """Blob key for the RUN-POINTER sidecar of a compiled artifact: a ``.run``
    sibling of the derived key (one rule for every variant, mirroring
    :func:`procedural_catalog_fp_key`) holding the id of the run that most
    recently targeted that key.

    It exists because two lookups have only the artifact key to go on: a compile
    the endpoint served straight from cache (no run happened now, but the panel
    still wants the log of the run that built those bytes) and an old artifact
    from before runs existed. The pointer is written when the run STARTS, not when
    it succeeds, so a run that fails before producing bytes is still findable."""
    return f"{derived_key}.run"


def prune_run_log_keys(keys_newest_first: list[str], keep: int = RUN_LOG_RETENTION) -> list[str]:
    """Split a model's run-log keys (already ordered newest-first) into the ones to
    delete. Retention is per model and generous — a run log is a few KB — but
    bounded, because runs accumulate without limit where the old artifact-keyed
    log overwrote itself in place. Pruning an old run's log only costs the admin
    audit row its "Log" tab; the row itself, with status/duration/error, remains."""
    return list(keys_newest_first[max(keep, 0) :])


def procedural_stats_key(glb_key: str) -> str:
    """Blob key for the quantity take-off STATS captured alongside a procedural GLB.

    A *sibling* of the GLB derived key — the same path with ``.stats.json`` swapped
    in for ``.glb`` — so a single rule covers every GLB variant (committed
    ``r{rev}.glb``, its ``_detail`` LOD, an engine-suffixed key, a
    ``preview/{hash}.glb``) and the stats key can never drift from the key the
    worker actually wrote the GLB to (mirrors :func:`procedural_log_key`). The
    frontend fetches it to populate the viewer's Stats panel; a model with no such
    sibling (a capability engine / STEP-IFC imports) degrades gracefully to "no take-off"."""
    if glb_key.endswith(".glb"):
        return f"{glb_key[: -len('.glb')]}.stats.json"
    return f"{glb_key}.stats.json"


def procedural_catalog_fp_key(derived_key: str) -> str:
    """Blob key for the CATALOG-FINGERPRINT sidecar of a compiled artifact — the
    plain-text fingerprint (:func:`ada.comms.rest.db.get_catalog_fingerprint`) of
    the equipment + system catalog state the artifact was built from. A ``.catfp``
    sibling of the derived key, so one rule covers every artifact variant (committed
    GLB, its ``_detail`` LOD, an engine-suffixed key, a ``preview/{hash}.glb``, and
    the ``.ifc``/``.gxml`` exports).

    The catalog is a LIVE compile input that the model's own revision does NOT
    capture: editing a placed equipment type (moving its ports, changing its
    bbox/mass, re-linking CAD) or a system template must produce a fresh model, but
    leaves the revision-stamped derived key unchanged. The compile/preview/export
    endpoints therefore compare the LIVE catalog fingerprint against this sidecar:
    a match serves the cached artifact; a mismatch (or a missing sidecar, e.g. an
    artifact built before this feature) forces a recompile that overwrites the key.
    A catalog-independent model (no equipment, no systems) never writes it — its
    cache stays purely revision/doc-hash keyed, byte-identical to before."""
    return f"{derived_key}.catfp"


def procedural_stats_xlsx_key(glb_key: str) -> str:
    """Blob key for the take-off stats exported as an Excel workbook — a
    ``.stats.xlsx`` sibling of the GLB derived key (whole-model, multi-sheet)."""
    if glb_key.endswith(".glb"):
        return f"{glb_key[: -len('.glb')]}.stats.xlsx"
    return f"{glb_key}.stats.xlsx"


def procedural_relocations_key(model_id: str) -> str:
    """Blob key for a model's latest relocation proposals (a JSON document). NOT
    revision-stamped: the proposal search always re-runs (the layout may have
    changed and it's cheap-ish), overwriting the previous result in place."""
    return f"{PROCEDURAL_PREFIX}{model_id}/relocations.json"


# ── Excel round-trip (export / import) ────────────────────────────────
#
# A procedural model can be exported to — and imported from — the OWNING
# engine's Excel workbook (adapy-default: ada.topo_model.excel; a capability
# engine: its own workbook). Both directions DELEGATE to the worker (the engine's
# capability pool owns the read/write), mirroring the compile/relocations
# synthetic-job pattern.


def procedural_xlsx_export_key(model_id: str, revision: int, engine: str | None = None) -> str:
    """Blob key for a model revision exported to its engine's Excel workbook.
    Revision- + engine-stamped so a re-export of an unchanged revision is served
    from cache and a non-default engine's workbook never collides with another's."""
    return f"{PROCEDURAL_PREFIX}{model_id}/r{revision}{_engine_suffix(engine)}.xlsx"


def procedural_model_export_key(model_id: str, revision: int, fmt: str, *, cad_equipment: bool = True) -> str:
    """Blob key for a model revision exported to a downloadable CAD/analysis file.

    ``fmt`` is ``"ifc"`` (the DETAIL model — beams/plates/joints with the clash
    cuts as IfcRelVoidsElement voids, equipment as IfcPump/IfcTank/…) or ``"gxml"``
    (the SIMULATION model as a Genie concept XML). Revision-stamped so a re-export
    of an unchanged revision is served from cache. ``cad_equipment`` (IFC only)
    distinguishes the CAD-spliced variant from the placeholder-box one so the two
    never collide in cache. Only the built-in engine builds an in-process adapy
    assembly, so these are adapy-default only."""
    variant = "" if (fmt != "ifc" or cad_equipment) else "_box"
    return f"{PROCEDURAL_PREFIX}{model_id}/r{revision}{variant}.{fmt}"


# Hidden staging prefix for an IMPORT upload: the workbook has no model yet, so
# it lands under a per-upload token (not a model id) until the import job parses
# it into a fresh model. Sits under PROCEDURAL_PREFIX so it's hidden + GC-able.
def procedural_import_source_key(token: str) -> str:
    """Blob key an uploaded (to-be-imported) workbook is staged at, keyed by a
    per-upload token."""
    safe = "".join(c if (c.isalnum() or c in "-_") else "-" for c in token)
    return f"{PROCEDURAL_PREFIX}_import/{safe}/source.xlsx"


def procedural_import_result_key(source_key: str) -> str:
    """Blob key for an import job's JSON result (``{model_id, name, engine,
    revision}``) — a sibling of the staged workbook, so one token groups the pair."""
    if source_key.endswith("source.xlsx"):
        return source_key[: -len("source.xlsx")] + "result.json"
    return source_key + ".result.json"


# The dedicated, engine-agnostic metadata sheet every EXPORTED workbook carries,
# so an IMPORT auto-detects which engine owns the file's format (and can warn on
# a schema/package drift). A hand-made / legacy workbook has no such sheet — the
# frontend then PROMPTS the user to pick an engine. Vertical key/value layout
# (column A = key, column B = value), no header row.
ADA_META_SHEET = "_ADA_META"
# Version of the _ADA_META sheet FORMAT itself (independent of the doc schema /
# the engine package version). Bump on an incompatible layout change.
ADA_META_VERSION = "1"
# Stable, versioned key names (column A). Keep stable across releases.
ADA_META_KEY_META_VERSION = "ada_meta_version"
ADA_META_KEY_ENGINE = "engine"
ADA_META_KEY_PACKAGE = "package"
ADA_META_KEY_PACKAGE_VERSION = "package_version"
ADA_META_KEY_SCHEMA_VERSION = "schema_version"
ADA_META_KEY_EXPORTED_AT = "exported_at"


def _xlsx_cell_text(cell, shared: list[str], local) -> str | None:
    """Resolve one worksheet ``<c>`` element's text: an ``inlineStr``, a shared-
    string index (``t="s"``), or a bare ``<v>`` value."""
    t = cell.get("t")
    if t == "inlineStr":
        return "".join(x.text or "" for x in cell.iter() if local(x.tag) == "t") or None
    v = None
    for ch in cell:
        if local(ch.tag) == "v":
            v = ch.text
            break
    if v is None:
        return None
    if t == "s":
        try:
            return shared[int(v)]
        except (ValueError, IndexError):
            return None
    return v


def read_ada_meta_from_xlsx_bytes(data: bytes) -> dict | None:
    """Read the ``_ADA_META`` sheet from xlsx BYTES using ONLY the standard library
    (zipfile + ElementTree), so the slim API (which has no openpyxl / ada) can
    auto-detect the exporting engine on import.

    Returns a ``{key: value}`` map (str -> str) for the sheet's key/value rows, or
    ``None`` when the workbook has no ``_ADA_META`` sheet (a hand-made / legacy
    file — the caller then prompts for an engine). Never raises on a malformed
    upload: any parse error resolves to ``None``."""
    import io
    import posixpath
    import xml.etree.ElementTree as ET
    import zipfile

    def _local(tag: str) -> str:
        return tag.rsplit("}", 1)[-1]

    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = set(zf.namelist())
            if "xl/workbook.xml" not in names:
                return None
            wb = ET.fromstring(zf.read("xl/workbook.xml"))
            rid = None
            for sheet in wb.iter():
                if _local(sheet.tag) != "sheet" or (sheet.get("name") or "").strip() != ADA_META_SHEET:
                    continue
                for k, v in sheet.attrib.items():
                    if _local(k) == "id":
                        rid = v
                        break
                break
            if rid is None:
                return None
            target = None
            if "xl/_rels/workbook.xml.rels" in names:
                rels = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
                for rel in rels.iter():
                    if _local(rel.tag) == "Relationship" and rel.get("Id") == rid:
                        target = rel.get("Target")
                        break
            if not target:
                return None
            tpath = target.lstrip("/") if target.startswith("/") else posixpath.normpath(posixpath.join("xl", target))
            if tpath not in names:
                return None
            shared: list[str] = []
            if "xl/sharedStrings.xml" in names:
                sst = ET.fromstring(zf.read("xl/sharedStrings.xml"))
                for si in sst:
                    if _local(si.tag) != "si":
                        continue
                    shared.append("".join(t.text or "" for t in si.iter() if _local(t.tag) == "t"))
            ws = ET.fromstring(zf.read(tpath))
            out: dict[str, str] = {}
            for row in ws.iter():
                if _local(row.tag) != "row":
                    continue
                key = val = None
                for c in row:
                    if _local(c.tag) != "c":
                        continue
                    col = "".join(ch for ch in (c.get("r") or "") if ch.isalpha())
                    text = _xlsx_cell_text(c, shared, _local)
                    if col == "A":
                        key = text
                    elif col == "B":
                        val = text
                if key and str(key).strip():
                    out[str(key).strip()] = val.strip() if isinstance(val, str) else val
            return out or None
    except Exception:
        return None


@lru_cache(maxsize=1)
def _doc_model():
    # Lazy: ada.topology.entities imports ada, which is heavy — only pay for it
    # on the first commit/compile, not at API boot.
    from typing import Optional

    from pydantic import BaseModel, ConfigDict, Field

    from ada.topo_model.engines import DEFAULT_ENGINE_SLUG, PROCEDURAL_SCHEMA_VERSION

    # TopoSystem/SystemConnection are the importable, xlsx-ready twins of the
    # local closure classes this used to declare — reused here so the wire format
    # has a single source of truth (they carry ClassVars but dump the same shape).
    from ada.topology.entities import (
        TopoEquipment,
        TopoLoftMember,
        TopoOpening,
        TopoSpace,
        TopoStructure,
        TopoSystem,
    )

    class CellGroup(BaseModel):
        """One cell GROUP — a named structure with its own blueprint. A space
        names its group via ``STRUCTURE_NAME``; ungrouped spaces omit it. Only an
        engine advertising ``supports_grouping`` acts on these."""

        # extra="allow" for the same reason as ProceduralDoc below: a grouping
        # engine may hang its own per-group settings here and we must not eat them.
        model_config = ConfigDict(extra="allow")

        name: str
        blueprint: Optional[str] = None

    class ProceduralDoc(BaseModel):
        # A procedural document is not adapy's private struct — it is authored by
        # whichever engine owns the model (built-in or external) and round-tripped
        # by the viewer, which stores UI state on it. pydantic's DEFAULT of
        # extra="ignore" made this model a lossy filter over every such document:
        # any key not declared below was deleted by validate_doc without a word.
        #
        # That silent delete is not a missing value, it is a CACHE COLLISION.
        # doc_content_hash() hashes this normalized doc to key the preview blob, so
        # two documents differing only in a dropped key hash IDENTICALLY and the
        # viewer is served the previously built GLB — a control that changes
        # nothing, and no error anywhere to say so. That is exactly how the
        # Blueprint dropdown (``blueprint_name``) stayed broken.
        #
        # So: preserve what we do not understand. Unknown keys round-trip and
        # participate in the hash. Declared fields below are still validated.
        model_config = ConfigDict(extra="allow")

        # Routing/identity header (see ada.topo_model.engines.EngineBinding):
        # ``engine`` selects the procedural engine that compiles this model and
        # ``schema_version`` records the doc-schema it was authored against. Both
        # are mirrored onto the procedural_models row so a compile auto-routes.
        engine: str = DEFAULT_ENGINE_SLUG
        schema_version: str = PROCEDURAL_SCHEMA_VERSION
        grid: dict = Field(default_factory=dict)
        # blueprint compile options (whitelisted by the compiler), e.g.
        # {"reinforce_internal_walls": true}
        blueprint: dict = Field(default_factory=dict)
        # The structural blueprint the user picked in the viewer's Blueprint
        # dropdown, as a top-level scalar (kept OUT of the ``blueprint`` options
        # dict, which is a whitelist of per-blueprint parameters).
        #
        # It MUST be declared here even though nothing in this module reads it.
        # pydantic defaults to ``extra="ignore"``, so an undeclared key is
        # silently dropped by ``validate_doc`` -- and that drop was invisible
        # twice over. The compiler reads ``doc.get("blueprint_name")`` and, not
        # finding it, fell back to the engine default; and ``doc_content_hash``
        # hashes this normalized doc, so every blueprint produced the SAME
        # preview cache key and the viewer served the previously built GLB back.
        # The net effect was a Blueprint dropdown that changed nothing, with no
        # error anywhere -- switching ENGINE appeared to work only because
        # ``engine`` is declared.
        blueprint_name: Optional[str] = None
        # named design ruleset (routing/penetration rules) resolved by the
        # compiler via ada.topo_model.resolve_design_rules; unknown -> standard
        design_rules: Optional[str] = None
        # selected fabrication-detail engine slug (adds connection joints after the
        # structural build); None/"none" = structural-only. Persisted so a model
        # (or a template) carries its detailing intent across open/commit; the
        # compile still reads the effective value from the job's conversion_options
        # (which the cellbuilder seeds from this on open).
        detailing: Optional[str] = None
        # when true, catalog equipment with a linked CAD asset render as the
        # real CAD geometry (spliced in at compile) instead of a box
        equipment_cad: bool = False
        # blob key of the original source workbook this model was imported from
        # (see procedural_source_key). A full-fidelity engine compiles the source
        # directly (all config the topology sheets below drop); None for a
        # cellbuilder-authored model.
        source_xlsx_key: Optional[str] = None
        spaces: list[TopoSpace] = Field(default_factory=list)
        equipments: list[TopoEquipment] = Field(default_factory=list)
        openings: list[TopoOpening] = Field(default_factory=list)
        # routed service runs between equipment ports; the compiler wires,
        # routes and renders these (see ada.topo_model.compile)
        systems: list[TopoSystem] = Field(default_factory=list)
        # swept ("lofted") members: ordered section-profile stacks that compile
        # into inter-station band cells + plates (see ada.topo_model.builder)
        loft_members: list[TopoLoftMember] = Field(default_factory=list)

        # --- fields the rest of the codebase READS off a normalized doc ------ #
        # Everything below was already produced and/or consumed somewhere, but was
        # never declared here — so validate_doc deleted each one on its way
        # through. extra="allow" above now preserves them regardless; they are
        # declared anyway so the shape is validated and the contract is written
        # down. All are Optional/None-defaulted so exclude_none keeps a document
        # that never used them BYTE-IDENTICAL — and therefore hash-identical, so
        # no existing preview/revision cache key moves.

        # Cell groups authored by the viewer's cellbuilder (``toDoc``) and read
        # back by it on open (``groupsFromDoc``). Dropping these did not just lose
        # the grouping: two docs that differed only in a group's blueprint hashed
        # the SAME, so the preview served the other group's cached GLB.
        groups: Optional[list[CellGroup]] = None
        # Multi-structure models: one topology model per entry, each placed at its
        # own origin (see ProceduralMultiBuilder). ProceduralBuilder.to_doc emits
        # these and from_dict reads them back, so an xlsx import of a workbook with
        # a ``Structures`` sheet went through validate_doc and came out single-
        # structure.
        structures: Optional[list[TopoStructure]] = None
        # Block INTERIOR walls from in-plane routing as well as exterior ones
        # (read by ada.topo_model.compile._build_systems, written by
        # ProceduralBuilder.to_doc, carried by the workbook's NO_GO_WALLS column).
        no_go_walls: Optional[bool] = None
        # Per-joint detailing option map ``{slug: {enabled, <field>: value}}``.
        # Normally threaded as a query param, but ProceduralBuilder.from_dict
        # accepts it off the document as a fallback, which validate_doc removed.
        detailing_options: Optional[dict] = None

    return ProceduralDoc


def _validate_doc_shallow(doc: dict) -> dict:
    """Structural check for slim API deployments where ada (numpy) is not
    installed: list fields hold objects with a string NAME. Full pydantic
    validation then happens on the worker at compile time."""
    # Start from the document itself so a key this shallow path does not know
    # about SURVIVES, matching ProceduralDoc's extra="allow". Building `out` from
    # scratch made this function a second, independent whitelist — so the slim API
    # image silently dropped keys the full path kept, and (because
    # doc_content_hash runs over the result) collided their cache keys. The
    # validated values below overwrite these passthrough copies.
    #
    # This subsumes the explicit ``blueprint_name`` carry that fixed the Blueprint
    # dropdown: passthrough keeps it when present and, unlike naming it here,
    # leaves it ABSENT when it is not -- which is what the pydantic path does via
    # exclude_none, so the two paths now hash identically either way.
    out = dict(doc)
    out.update(
        {
            "grid": doc.get("grid") or {},
            "blueprint": doc.get("blueprint") or {},
            "equipment_cad": bool(doc.get("equipment_cad")),
        }
    )
    # Routing header — mirror EngineBinding's defaults here (ada isn't importable in
    # the slim image, so the constants can't be pulled from ada.topo_model.engines).
    engine = doc.get("engine")
    if engine is not None and not isinstance(engine, str):
        raise ValueError("engine must be a string")
    out["engine"] = engine or "adapy-default"
    schema_version = doc.get("schema_version")
    if schema_version is not None and not isinstance(schema_version, str):
        raise ValueError("schema_version must be a string")
    out["schema_version"] = schema_version or "1.0"
    source_xlsx_key = doc.get("source_xlsx_key")
    if source_xlsx_key is not None:
        if not isinstance(source_xlsx_key, str):
            raise ValueError("source_xlsx_key must be a string")
        out["source_xlsx_key"] = source_xlsx_key
    design_rules = doc.get("design_rules")
    if design_rules is not None:
        if not isinstance(design_rules, str):
            raise ValueError("design_rules must be a string")
        out["design_rules"] = design_rules
    detailing = doc.get("detailing")
    if detailing is not None:
        if not isinstance(detailing, str):
            raise ValueError("detailing must be a string")
        out["detailing"] = detailing
    if not isinstance(out["grid"], dict):
        raise ValueError("grid must be an object")
    if not isinstance(out["blueprint"], dict):
        raise ValueError("blueprint must be an object")
    for key in ("spaces", "equipments", "openings", "systems", "loft_members"):
        entries = doc.get(key) or []
        if not isinstance(entries, list):
            raise ValueError(f"{key} must be a list")
        for i, entry in enumerate(entries):
            if not isinstance(entry, dict) or not isinstance(entry.get("NAME"), str) or not entry["NAME"]:
                raise ValueError(f"{key}[{i}] must be an object with a non-empty NAME")
        out[key] = entries
    # Mirror the full path's exclude_none so the two normalizers agree key-for-key
    # on the same input (a null passed through would otherwise show up here and
    # not there, moving the content hash between the slim and full images).
    return {k: v for k, v in out.items() if v is not None}


def validate_doc(doc: dict) -> dict:
    """Validate + normalize a procedural document by round-tripping it through
    the pydantic entity models. Raises ValueError with the pydantic error text
    on invalid input. Falls back to shallow structural validation when ada is
    not importable (slim API image)."""
    import pydantic

    if not isinstance(doc, dict):
        raise ValueError(f"doc must be an object, got {type(doc).__name__}")
    try:
        doc_model = _doc_model()
    except ImportError:
        return _validate_doc_shallow(doc)
    try:
        model = doc_model(**doc)
    except pydantic.ValidationError as e:
        raise ValueError(str(e)) from None
    # exclude_none: Topo* entities type several fields as `float` with a None
    # DEFAULT (e.g. TopoOpening.X/Y/Z/DX/DY/DZ). pydantic accepts None as a
    # default but REJECTS an explicit None passed to the constructor — so a dump
    # that re-injects nulls makes every downstream `TopoOpening(**o)` /
    # `TopoEquipment(**e)` reconstruction (adapy-default compile.py + a capability
    # engine's procedural_engine) raise, silently dropping equipment/openings. Dropping
    # None-valued keys lets field defaults reapply cleanly on reconstruction.
    return model.model_dump(mode="json", exclude_none=True)
