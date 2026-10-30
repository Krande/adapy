"""Provider-declared schedules and change checks -- the ``asset_schedules`` part of the asset-provider seam.

A provider declares on its plugin spec, next to ``asset_collection_request`` and friends, the
jobs it offers for scheduling::

    asset_schedules=[{
        "id": "changes", "kind": "change-check", "label": "...", "description": "...",
        "options": {"action": "..."},          # fixed job options
        "collection_option": "project",        # the option that receives the collection
        "settings": [{"name": "exclude_users", "choices": "change_users"}],
    }]

Two surfaces read it:

* **Admin → Providers → Scheduled jobs** (``admin_router``): job × collection × frequency ×
  settings, every one a choice -- nothing here accepts free-text options. A row is stored in
  ``plugin_job_schedules`` (the scheduler that already runs those fires it) with the provider,
  collection and entry id beside it, so its options are rebuilt from the LIVE declaration at every
  firing rather than frozen at creation.
* **Sources tab → Check for changes** (``router``): runs the provider's ``change-check`` entry
  now. An admin operation, like every provider job.

NO SCOPE. Every job of an asset provider runs in the provider store (``Scope.providers()``,
``routes/provider_store.py``): provider data is kept once and read by scopes through grants. A
schedule therefore has no scope to choose -- ``scope`` is accepted and ignored, new rows store
``providers``, and a row from before (still naming ``shared`` or a project) fires into the store
all the same. One live schedule per (provider, collection, job).

An ``apply-changes`` entry (docs/design/apply-changes.md) is scheduled here like any other -- its
targets a MULTI choice (``"multiple": true`` on the ``job_options`` declaration, a list of declared
choices, never empty) -- and may name no collection: then it runs for every collection the provider
offers. It fires as a sweep run does (``routes/asset_apply_changes.py``), and its rows in the
listing carry core's cursor per collection (``cursors``).

Every change check, from either, gets an ``asset_change_runs`` row, keyed under the store. Its job
answers with the provider-neutral ``asset_changes`` block (``ada.assets/changes@1``), which this
module copies in once the job is done. Report only: nothing is published as a consequence. A scope
lists the store's runs of the datasets granted to it, beside its own rows from before the store.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from ada.config import logger

from .. import auth as auth_module
from .. import db as db_module
from ..auth import User
from ..scope import Scope
from .deps import (
    RestContext,
    SystemUser,
    next_fire,
    parse_scope,
    require_pool,
    resolve_project_scope,
    rest_context,
    scope_from_path,
)
from .plugin_jobs import enqueue_plugin_job
from .provider_store import (
    PROVIDER_STORE,
    PROVIDER_STORE_WIRE,
    granted_covers,
    job_scope,
    readable_store_datasets,
    require_admin_for_provider_job,
)

router = APIRouter()
admin_router = APIRouter()

CHANGE_CHECK = "change-check"
#: Every collection the provider offers, one job each, each published -- see routes/asset_sweeps.py.
SWEEP = "sweep"
#: Only what changed since core's cursor, per collection, applied as element-level patches --
#: driven like a sweep. See routes/asset_apply_changes.py and docs/design/apply-changes.md.
APPLY_CHANGES = "apply-changes"
KINDS = (CHANGE_CHECK, "job", SWEEP, APPLY_CHANGES)
#: Kinds whose row may name no collection: the driver supplies each one in turn.
COLLECTIONLESS_KINDS = (SWEEP, APPLY_CHANGES)
#: The multi-choice setting of an apply-changes entry: which targets to patch. Never empty.
APPLY_TARGETS = "apply_targets"
#: A setting's ``choices`` value asking core to offer the users earlier change checks reported.
CHOICES_CHANGE_USERS = "change_users"
CHANGES_SCHEMA = "ada.assets/changes@1"
ITEMS_SCHEMA = "ada.assets/change-items@1"

#: The frequencies an admin picks from. UTC, like every schedule here. A preset rather than a
#: cron field: the point of this surface is that nothing on it is free text.
FREQUENCIES: dict[str, dict[str, str]] = {
    "hourly": {"label": "Every hour", "cron": "0 * * * *"},
    "every-4h": {"label": "Every 4 hours", "cron": "0 */4 * * *"},
    "daily": {"label": "Daily at 02:00 UTC", "cron": "0 2 * * *"},
    "weekdays": {"label": "Weekdays at 02:00 UTC", "cron": "0 2 * * 1-5"},
    "weekly": {"label": "Weekly, Monday 02:00 UTC", "cron": "0 2 * * 1"},
}


# --- the declaration ---------------------------------------------------------------------


def schedule_entries(spec: dict) -> list[dict]:
    """The spec's well-formed ``asset_schedules`` entries. A malformed one is dropped with a log
    line rather than failing every listing: one plugin's typo must not hide the others."""
    raw = spec.get("asset_schedules")
    if not isinstance(raw, list):
        return []
    out, seen = [], set()
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        sid = str(entry.get("id") or "").strip()
        kind = entry.get("kind") or "job"
        options = entry.get("options")
        if not sid or sid in seen or kind not in KINDS or not isinstance(options, dict):
            logger.warning("asset_schedules: ignoring a malformed entry on %s: %r", spec.get("slug"), entry)
            continue
        if kind in (CHANGE_CHECK, SWEEP, APPLY_CHANGES) and not entry.get("collection_option"):
            logger.warning("asset_schedules: %s %r on %s names no collection_option", kind, sid, spec.get("slug"))
            continue
        seen.add(sid)
        out.append(entry)
    return out


def _plugin_id(spec: dict) -> str:
    return str(spec.get("slug") or spec.get("id") or "")


async def provider_specs(ctx: RestContext) -> dict[str, list[dict]]:
    """Live plugin specs that declare schedules, grouped by ``asset_provider_id``."""
    out: dict[str, list[dict]] = {}
    for spec in (await ctx.jobs.advertised_specs("plugin_specs")).values():
        provider = spec.get("asset_provider_id")
        if isinstance(provider, str) and provider and schedule_entries(spec):
            out.setdefault(provider, []).append(spec)
    return out


def find_entry(specs: list[dict], job: str | None = None, *, kind: str | None = None) -> tuple[dict, dict] | None:
    """``(spec, entry)`` for a provider's entry by id, or its entry of ``kind``. The first
    declaring spec wins, as for every other asset-provider key."""
    for spec in specs:
        for entry in schedule_entries(spec):
            if (job is not None and entry["id"] == job) or (kind is not None and entry.get("kind") == kind):
                return spec, entry
    return None


def collections_of(spec: dict) -> list[str]:
    field = spec.get("asset_collections_field")
    values = spec.get(field) if isinstance(field, str) else None
    return [str(v) for v in values] if isinstance(values, list) else []


# --- per-collection choices an admin may override ------------------------------------------------
#
# A provider may find, per collection, several sources it could read it from -- several copies of a
# project, say -- and pick one itself. It declares
#
#     asset_collection_choices=[{"option": "project_root", "label": "Share", "field": "project_locations"}]
#
# and in the spec field named there, a dict keyed by collection, listing only collections with two
# or more choices: ``{"ABC": {"default": v, "choices": [{"value": v, "label": "..."}, ...]}}`` (a
# ``union_fields`` key, merged across workers by ``deps._merge_keyed``). An admin may pin one per
# collection on any sweep / apply-changes / collection-request run or schedule -- core's own setting
# ``collection_choices: {option: {collection: value}}``, holding ONLY picks that differ from the
# worker's default. The driver sends ``options[option] = pick`` for the collection; without a pick
# the worker decides. Core never names a provider here, and never sends an option a provider does
# not declare.

#: Core's setting holding the admin's per-collection picks.
COLLECTION_CHOICES = "collection_choices"


def collection_choice_decls(specs: list[dict] | dict) -> list[dict]:
    """``[{option, label, collections: {collection: {default, choices: [{value, label}]}}}]`` -- what
    the provider declares, its choices merged over all of its specs (first spec's default wins)."""
    from .deps import _merge_keyed

    out: dict[str, dict] = {}
    for spec in [specs] if isinstance(specs, dict) else specs:
        for decl in spec.get("asset_collection_choices") or ():
            if not isinstance(decl, dict):
                continue
            option, field = decl.get("option"), decl.get("field")
            if not (isinstance(option, str) and option and isinstance(field, str) and field):
                continue
            values = spec.get(field) if isinstance(spec.get(field), dict) else {}
            clean = {
                str(c): {
                    "default": v.get("default"),
                    "choices": [x for x in v.get("choices") or () if isinstance(x, dict) and "value" in x],
                }
                for c, v in values.items()
                if isinstance(v, dict)
            }
            have = out.setdefault(
                option, {"option": option, "label": str(decl.get("label") or option), "collections": {}}
            )
            have["collections"] = _merge_keyed(have["collections"], clean)
    for d in out.values():
        d["collections"] = {c: v for c, v in d["collections"].items() if len(v.get("choices") or ()) >= 2}
    return list(out.values())


def _advertised(decl: dict, collection: str) -> dict | None:
    """The declaration's entry for ``collection`` (matched case-insensitively), or None."""
    want = collection.lower()
    for c, v in decl["collections"].items():
        if c.lower() == want:
            return v
    return None


def clean_collection_choices(decls: list[dict], raw) -> dict:
    """An admin's ``{option: {collection: value}}`` checked against what the provider advertises:
    a pick of the worker's default is dropped (no override), a value that is not one of the
    collection's choices is refused (400, naming the valid ones), an option the provider does not
    declare is refused. A pick for a collection the provider does not advertise (any more) is kept
    as it is -- the run reports it and does not send it (:func:`collection_choice_options`)."""
    if raw in (None, {}):
        return {}
    if not isinstance(raw, dict):
        raise HTTPException(status_code=400, detail=f"{COLLECTION_CHOICES} must be {{option: {{collection: value}}}}")
    by_option = {d["option"]: d for d in decls}
    out: dict[str, dict] = {}
    for option, picks in raw.items():
        decl = by_option.get(option)
        if decl is None:
            raise HTTPException(
                status_code=400, detail=f"{option!r} is not a per-collection choice the provider declares"
            )
        if not isinstance(picks, dict):
            raise HTTPException(status_code=400, detail=f"{COLLECTION_CHOICES}.{option} must be {{collection: value}}")
        kept: dict[str, Any] = {}
        for collection, value in picks.items():
            if value in (None, ""):
                continue
            entry = _advertised(decl, str(collection))
            if entry is None:
                kept[str(collection)] = value  # no longer advertised: kept, reported at run time
                continue
            if same_choice(value, entry.get("default")):
                continue
            valid = [c.get("value") for c in entry.get("choices") or ()]
            if not any(same_choice(value, v) for v in valid):
                raise HTTPException(
                    status_code=400,
                    detail=f"{decl['label']} for {collection}: {value!r} is not one of {', '.join(map(repr, valid))}",
                )
            kept[str(collection)] = value
        if kept:
            out[option] = kept
    return out


def collection_choice_options(decls: list[dict], picks, collection: str) -> tuple[dict, list[str]]:
    """``(options to add, warnings)`` for one collection's provider job: ``{option: pick}`` for each
    pick the provider still advertises for it; a pick for a collection (or a value) it no longer
    advertises is not sent, and said."""
    options: dict = {}
    warnings: list[str] = []
    if not isinstance(picks, dict):
        return options, warnings
    by_option = {d["option"]: d for d in decls}
    for option, per in picks.items():
        if not isinstance(per, dict):
            continue
        value = next((v for c, v in per.items() if str(c).lower() == collection.lower()), None)
        if value in (None, ""):
            continue
        decl = by_option.get(option)
        entry = _advertised(decl, collection) if decl else None
        label = decl["label"] if decl else option
        if entry is None:
            warnings.append(
                f"{label} {value!r} was chosen for {collection}, which the provider no longer offers a choice "
                "for; not sent (the worker decides)"
            )
            continue
        if not any(same_choice(value, c.get("value")) for c in entry.get("choices") or ()):
            warnings.append(f"{label} {value!r} for {collection} is no longer offered; not sent (the worker decides)")
            continue
        options[option] = value
    return options, warnings


def _declared_option(spec: dict, name: str) -> dict:
    for decl in spec.get("job_options") or ():
        if isinstance(decl, dict) and decl.get("name") == name:
            return decl
    return {}


def _choice_label(labels: dict, value: Any) -> str:
    """A declared value's label. Keys are ``str(value)`` -- so a ``bool`` option's are Python's
    ``"False"``/``"True"`` -- with the JSON spelling (``"false"``) accepted as well, since a
    declaration written by hand in another language would naturally use that."""
    for key in (str(value), json.dumps(value)):
        if key in labels:
            return str(labels[key])
    if isinstance(value, bool):
        return "Yes" if value else "No"
    return str(value)


_BOOL_WORDS = {"true": True, "false": False}


def same_choice(value: Any, choice: Any) -> bool:
    """Whether a submitted ``value`` is the declared ``choice``. Compared by value, tolerant of
    how a bool travelled: ``False``, ``"false"`` and ``"False"`` are all the declared ``False``
    (a form, a query string or an older stored schedule may carry any of them). Everything else
    compares as text, as it always has -- ``3`` and ``"3"`` are the same choice."""
    if isinstance(choice, bool) or isinstance(value, bool):
        as_bool = [
            x if isinstance(x, bool) else _BOOL_WORDS.get(x.strip().lower()) if isinstance(x, str) else None
            for x in (value, choice)
        ]
        return as_bool[0] is not None and as_bool[0] is as_bool[1]
    return str(value) == str(choice)


def typed_choice(value: Any, choices: list[dict]) -> tuple[bool, Any]:
    """``(found, declared value)`` for a submitted value: the choice it names, in the type the
    provider declared it, so ``"false"`` is stored -- and reaches the job -- as ``False``."""
    for c in choices:
        if same_choice(value, c.get("value")):
            return True, c.get("value")
    return False, value


def entry_settings(spec: dict, entry: dict) -> list[dict]:
    """The settings an admin may choose for ``entry``, each merged with the spec's ``job_options``
    declaration. ``source`` says where the choices come from: ``enum`` (declared), ``spec``
    (``enum_from``: a spec list) or ``change_users`` (earlier runs).

    A declaration with ``"multiple": true`` is a MULTI choice (checkboxes): the setting says
    ``"multiple": true``, its value is a list of declared choices, and its ``default`` is a list."""
    out = []
    for raw in entry.get("settings") or ():
        item = {"name": raw} if isinstance(raw, str) else dict(raw) if isinstance(raw, dict) else None
        if not item or not item.get("name"):
            continue
        decl = _declared_option(spec, str(item["name"]))
        setting = {
            "name": str(item["name"]),
            "type": decl.get("type") or "enum",
            "title": item.get("title") or decl.get("title") or str(item["name"]),
            "description": item.get("description") or decl.get("description"),
            "default": decl.get("default"),
            "multiple": decl.get("multiple") is True,
        }
        if setting["multiple"] and setting["default"] is not None and not isinstance(setting["default"], list):
            setting["default"] = [setting["default"]]
        if item.get("choices") == CHOICES_CHANGE_USERS:
            setting["source"] = CHOICES_CHANGE_USERS
        elif isinstance(decl.get("enum"), list):
            setting["source"] = "enum"
            labels = decl.get("labels") if isinstance(decl.get("labels"), dict) else {}
            setting["choices"] = [{"value": v, "label": _choice_label(labels, v)} for v in decl["enum"]]
        elif isinstance(decl.get("enum_from"), str):
            setting["source"] = "spec"
            setting["choices"] = [{"value": v, "label": str(v)} for v in spec.get(decl["enum_from"]) or () if v]
        else:
            # No choices anywhere: not offered. This surface never falls back to free text.
            logger.info(
                "asset_schedules: setting %r on %s has no choices; not offered", setting["name"], spec.get("slug")
            )
            continue
        out.append(setting)
    if entry.get("kind") == APPLY_CHANGES:
        out.append(dict(MISSING_FULL_EXPORT_SETTING, choices=[dict(c) for c in MISSING_FULL_EXPORT_SETTING["choices"]]))
    return out


def targets_setting(spec: dict, entry: dict) -> str | None:
    """The name of a sweep's TARGETS setting -- ``"targets_setting": "sweep_target"`` on the entry,
    naming one of its settings that is a ``multiple`` enum -- or None: a sweep without one (or whose
    named setting is still a single choice) runs one job per collection, as it always has."""
    if (entry.get("kind") or "job") != SWEEP:
        return None
    name = entry.get("targets_setting")
    if not isinstance(name, str) or not name:
        return None
    for s in entry_settings(spec, entry):
        if s["name"] == name and s.get("multiple") and s.get("choices"):
            return name
    return None


def sweep_targets(spec: dict, entry: dict, settings: dict | None) -> list | None:
    """The targets one collection of a sweep runs, in the ENUM's declared order (the provider lists
    the tree first: what comes after reads the published tree), one provider job each. The chosen
    list -- a single stored value (a schedule from before the setting was multiple) is a list of one
    -- else the declared default, else every choice. None when the sweep has no targets setting."""
    name = targets_setting(spec, entry)
    if name is None:
        return None
    setting = next(s for s in entry_settings(spec, entry) if s["name"] == name)
    choices = [c["value"] for c in setting.get("choices") or ()]
    raw = (settings or {}).get(name)
    if raw in (None, "", []):
        raw = setting.get("default") if setting.get("default") not in (None, []) else choices
    chosen = raw if isinstance(raw, list) else [raw]
    ordered = [c for c in choices if any(same_choice(v, c) for v in chosen)]
    # A value the declaration no longer names still runs, after the declared ones: the provider
    # answers for it (as a stored single value always did).
    return ordered + [v for v in chosen if not any(same_choice(v, c) for c in choices)]


#: Targets that read what an earlier target PUBLISHES, so they cannot run once it failed in the same
#: collection: node bounds read the published tree. An entry may declare its own
#: ``"target_requires": {target: [targets]}``; this is the default for the asset vocabulary.
DEFAULT_TARGET_REQUIRES: dict[str, list[str]] = {"bounds": ["tree"]}


def target_requires(entry: dict) -> dict[str, list[str]]:
    declared = entry.get("target_requires")
    if isinstance(declared, dict):
        return {str(k): [str(x) for x in v] for k, v in declared.items() if isinstance(v, list)}
    return DEFAULT_TARGET_REQUIRES


#: CORE's own setting of every apply-changes entry -- no provider declares it, and it is never sent
#: to the provider's job: what to do about a collection the provider store holds no full export of
#: (such a collection is never asked for its changes; routes/asset_apply_changes.py).
MISSING_FULL_EXPORT = "missing_full_export"
MISSING_FULL_EXPORT_WARN = "warn"
MISSING_FULL_EXPORT_EXPORT = "export"
MISSING_FULL_EXPORT_SETTING: dict = {
    "name": MISSING_FULL_EXPORT,
    "type": "enum",
    "title": "Collections without a full export",
    "description": "A collection the provider store holds no full export of is left out of the run. Warn "
    "about it, or also queue a full export of it once the run has finished.",
    "default": MISSING_FULL_EXPORT_WARN,
    "multiple": False,
    "source": "enum",
    "core": True,
    "choices": [
        {"value": MISSING_FULL_EXPORT_WARN, "label": "Warn only"},
        {"value": MISSING_FULL_EXPORT_EXPORT, "label": "Queue a full project export"},
    ],
}
#: Settings core adds to an entry of a kind, beside what the provider declares.
CORE_SETTINGS: dict[str, tuple[str, ...]] = {APPLY_CHANGES: (MISSING_FULL_EXPORT,)}


async def setting_choices(pool, setting: dict, *, scope: str, provider: str, collection: str) -> list[dict]:
    if setting.get("source") == CHOICES_CHANGE_USERS:
        users = await db_module.asset_change_users(pool, scope=scope, provider=provider, collection=collection)
        return [{"value": u, "label": u} for u in users]
    return list(setting.get("choices") or ())


async def build_options(
    pool,
    spec: dict,
    entry: dict,
    *,
    scope: str,
    provider: str,
    collection: str | None,
    settings: dict | None,
    validate: bool = True,
) -> dict:
    """The job options for one entry: its fixed options, the collection, and the chosen settings.
    Every chosen value must be one of its setting's choices -- checked here, server side, so a
    hand-made request cannot smuggle free text past the dropdowns.

    A MULTI setting (``multiple``) is always a list: a single value is wrapped, an unset one takes
    the declared default list, and :data:`APPLY_TARGETS` refuses an empty list -- an apply-changes
    run that patches nothing would still advance its cursor past every change. So does a sweep's
    ``targets_setting`` (:func:`targets_setting`): a full export of nothing is not an export."""
    options = dict(entry["options"])
    required_lists = {APPLY_TARGETS, targets_setting(spec, entry)} - {None}
    collection_option = entry.get("collection_option")
    if collection_option and not collection and entry.get("kind") in COLLECTIONLESS_KINDS:
        # A sweep's (or an apply-changes of every collection's) row names no collection: the
        # driver supplies each one in turn.
        collection_option = None
    if collection_option:
        if not collection:
            raise HTTPException(status_code=400, detail=f"{entry['id']!r} needs a collection")
        if validate:
            offered = collections_of(spec)
            if offered and collection not in offered:
                raise HTTPException(
                    status_code=400,
                    detail=f"{collection!r} is not a collection {provider!r} offers ({', '.join(offered[:20])})",
                )
        options[str(collection_option)] = collection
    declared = {s["name"]: s for s in entry_settings(spec, entry)}
    settings = dict(settings or {})
    picks = settings.pop(COLLECTION_CHOICES, None)
    if picks not in (None, {}):
        # Core's per-collection overrides (see collection_choice_decls): only on runs that ask a
        # provider per collection. Stored with the options; the driver takes them off again.
        if (entry.get("kind") or "job") not in (SWEEP, APPLY_CHANGES):
            if validate:
                raise HTTPException(status_code=400, detail=f"{COLLECTION_CHOICES} is not a setting of {entry['id']!r}")
        else:
            cleaned = clean_collection_choices(collection_choice_decls(spec), picks) if validate else picks
            if cleaned:
                options[COLLECTION_CHOICES] = cleaned
    for name, value in settings.items():
        setting = declared.get(name)
        if setting is None:
            if validate:
                raise HTTPException(status_code=400, detail=f"{name!r} is not a setting of {entry['id']!r}")
            continue
        if setting.get("multiple") and isinstance(value, list) and not value and name in required_lists and validate:
            raise HTTPException(status_code=400, detail=f"{name}: tick at least one")
        if value is None or (isinstance(value, (list, str)) and len(value) == 0):
            # Unset. Note `False` is a choice, not "unset": a bool setting's off is a value.
            continue
        if setting.get("multiple") and not isinstance(value, list):
            value = [value]
        if validate:
            choices = await setting_choices(pool, setting, scope=scope, provider=provider, collection=collection or "")
        else:
            # The declared choices only -- no lookup of earlier runs on the firing path. A value
            # they do not name is passed through as stored.
            choices = list(setting.get("choices") or ())
        values = value if isinstance(value, list) else [value]
        typed, bad = [], []
        for v in values:
            found, declared_value = typed_choice(v, choices)
            typed.append(declared_value)
            if not found:
                bad.append(v)
        if validate and bad:
            raise HTTPException(status_code=400, detail=f"{name}: {bad!r} not among its choices")
        options[name] = typed if isinstance(value, list) else typed[0]
    for name, setting in declared.items():
        # A multi choice left unset takes its declared default list (a copy: never the spec's own).
        if setting.get("multiple") and name not in options and isinstance(setting.get("default"), list):
            options[name] = list(setting["default"])
        # A core setting left unset is stored as its default, so a run says what it did.
        elif setting.get("core") and name not in options and setting.get("default") is not None:
            options[name] = setting["default"]
    return options


def settings_of(entry: dict, options: dict) -> dict:
    """The chosen settings, read back out of stored job options -- the core settings of the
    entry's kind (:data:`CORE_SETTINGS`) included."""
    names = [s if isinstance(s, str) else (s or {}).get("name") for s in entry.get("settings") or ()]
    names += list(CORE_SETTINGS.get(entry.get("kind") or "", ()))
    if (entry.get("kind") or "job") in (SWEEP, APPLY_CHANGES):
        names.append(COLLECTION_CHOICES)
    return {n: options[n] for n in names if n and n in options}


def job_keys(plugin_id: str, options: dict) -> tuple[str, str]:
    """``(derived_key, derived_prefix)`` exactly as ``enqueue_plugin_job`` would derive the key --
    computed here so the run knows where its summary lands and the provider gets a prefix to
    write its item list under."""
    opts_hash = hashlib.sha256(json.dumps(options, sort_keys=True).encode("utf-8")).hexdigest()[:16]
    prefix = f"_derived/plugin_jobs/{plugin_id}/{opts_hash}"
    return f"{prefix}.json", prefix


async def start_change_check(
    ctx: RestContext,
    pool,
    *,
    scope_obj: Scope,
    scope: str,
    provider: str,
    collection: str,
    spec: dict,
    entry: dict,
    options: dict,
    user,
    via: str,
    schedule_id: str | None = None,
    request: Request | None = None,
) -> dict:
    """Enqueue one change check and record its run. ``options`` are final (built and stamped).

    The check runs in the provider store and its run is keyed there, whatever ``scope_obj`` /
    ``scope`` the caller passed (``job_scope``): the summary it is finished from is then read from
    the same scope the job wrote it in, and every scope granted the dataset sees the one run."""
    plugin_id = _plugin_id(spec)
    scope_obj = job_scope(spec, scope_obj)
    if scope_obj == PROVIDER_STORE:
        scope = PROVIDER_STORE.prefix()
    derived_key, derived_prefix = job_keys(plugin_id, options)
    job_id = await enqueue_plugin_job(
        ctx,
        plugin_id=plugin_id,
        options=options,
        scope_obj=scope_obj,
        user=user,
        pool=pool,
        request=request,
        derived_key=derived_key,
        derived_prefix=derived_prefix,
        plugin_spec=spec,
    )
    return await db_module.insert_asset_change_run(
        pool,
        scope=scope,
        provider=provider,
        collection=collection,
        plugin_id=plugin_id,
        job_id=job_id,
        derived_key=derived_key,
        requested_by=getattr(user, "sub", None),
        requested_via=via,
        schedule_id=schedule_id,
    )


def _block_of(summary: Any) -> dict | None:
    block = summary.get("asset_changes") if isinstance(summary, dict) else None
    return block if isinstance(block, dict) else None


def run_scope(run: dict, fallback: Scope | None = None) -> Scope:
    """The scope a run's job wrote in -- its summary and item list are read there. A run row keys
    the scope by storage prefix (``providers`` for every run since the store, the launching scope's
    prefix before); ``fallback`` answers for a row whose key does not parse."""
    try:
        return Scope.from_prefix(str(run.get("scope") or ""))
    except ValueError:
        if fallback is None:
            raise
        return fallback


async def finish_pending_runs(ctx: RestContext, pool, scope_obj: Scope | None, runs: list[dict]) -> list[dict]:
    """Copy the result of every finished job into its run. Best-effort per run: one unreadable
    summary must not fail the listing.

    Each summary is read from the scope the RUN belongs to (:func:`run_scope`), not the reader's:
    a scope lists the store's runs of its granted datasets beside its own, and an admin's table
    lists rows of both ages. ``scope_obj`` is only the fallback for a row that names no scope."""
    out = []
    for run in runs:
        if run["status"] != "queued" or not run.get("job_id"):
            out.append(run)
            continue
        try:
            snapshot = await ctx.jobs.status(run["job_id"])
            state = (snapshot.status if snapshot is not None else "").lower()
            finished = None
            if state == "done":
                try:
                    summary = json.loads(await ctx.storage.get_bytes(run_scope(run, scope_obj), run["derived_key"]))
                except Exception as exc:  # noqa: BLE001 - reported on the run
                    finished = await db_module.finish_asset_change_run(
                        pool, run["id"], status="error", error=f"the job's summary could not be read: {exc}"
                    )
                else:
                    block = _block_of(summary)
                    if block is None:
                        finished = await db_module.finish_asset_change_run(
                            pool,
                            run["id"],
                            status="error",
                            error="the job answered without an asset_changes block -- the provider does not "
                            "implement change checks in this version",
                        )
                    else:
                        finished = await db_module.finish_asset_change_run(pool, run["id"], status="done", block=block)
            elif state in ("error", "cancelled"):
                detail = (snapshot.payload or {}).get("error") if snapshot is not None else None
                finished = await db_module.finish_asset_change_run(
                    pool, run["id"], status="error", error=str(detail or f"the job was {state}")
                )
            out.append(finished or run)
        except Exception:  # noqa: BLE001 - see the docstring
            logger.exception("asset change runs: could not finish run %s", run.get("id"))
            out.append(run)
    return out


async def scope_key(pool, scope: str, user=None) -> str:
    """The canonical key runs are stored under: the scope's storage prefix, as the change feed
    keys its rows. A schedule keeps the scope as the admin chose it (``project:<slug>`` survives a
    rename); the Sources tab asks by id -- both resolve to the same key."""
    resolved = await resolve_project_scope(pool, parse_scope(scope, user or SystemUser()))
    return resolved.prefix()


def _public_run(run: dict) -> dict:
    return {k: v for k, v in run.items() if k not in ("derived_key", "items_key", "items")}


async def _specs_or_404(ctx: RestContext, provider: str) -> list[dict]:
    specs = (await provider_specs(ctx)).get(provider)
    if not specs:
        raise HTTPException(
            status_code=404,
            detail=f"no online worker advertises schedules for provider {provider!r}",
        )
    return specs


async def _saved_settings(pool, *, provider: str, collection: str, entry: dict) -> dict:
    """A manual check uses the settings of the scheduled check for the same collection, if there
    is one -- so excluding a batch account applies to both. One live schedule per collection since
    the store; of rows from before it (one per scope), a ``providers`` row wins, else the first."""
    rows = [
        row
        for row in await db_module.list_plugin_job_schedules(pool, asset_provider=provider)
        if row["collection"] == collection and row["schedule_job"] == entry["id"]
    ]
    rows.sort(key=lambda row: row.get("scope") != PROVIDER_STORE_WIRE)
    return settings_of(entry, rows[0]["options"]) if rows else {}


async def _store_runs_for(pool, scope_obj: Scope, *, provider: str | None, collection: str | None, limit: int):
    """The provider store's runs ``scope_obj`` may read: those of datasets granted to it with
    attributes or geometry. Filtered here rather than in SQL -- a scope holds a handful of grants --
    so the store is read a little wider than ``limit`` to leave room for what the filter drops."""
    datasets = await readable_store_datasets(pool, scope_obj)
    if provider:
        datasets = [d for d in datasets if d[0] == provider]
    if not datasets:
        return []
    runs = await db_module.list_asset_change_runs(
        pool,
        scope=PROVIDER_STORE.prefix(),
        provider=provider or None,
        collection=collection or None,
        limit=min(max(limit * 4, 50), 400),
    )
    return [r for r in runs if granted_covers(datasets, r["provider"], r["collection"])]


async def _may_read_run(pool, scope_obj: Scope, run: dict) -> bool:
    """Whether ``scope_obj`` may read ``run``: its own row, or a store run of a dataset granted to it."""
    if run["scope"] == scope_obj.prefix():
        return True
    if run["scope"] != PROVIDER_STORE.prefix():
        return False
    return granted_covers(await readable_store_datasets(pool, scope_obj), run["provider"], run["collection"])


# --- Sources tab: check now, list runs, read a run's items ---------------------------------


@router.post("/scopes/{scope}/asset-changes/check")
async def api_asset_changes_check(
    request: Request,
    scope_obj: Scope = Depends(scope_from_path),
    user: User = Depends(auth_module.current_user),
    ctx: RestContext = Depends(rest_context),
) -> JSONResponse:
    """Run a provider's change check for one collection now. Body: ``{provider, collection}``.

    ADMIN ONLY, and it runs in the provider store whichever scope it is asked from: a change check
    is a provider job, and provider data is kept current by admins. The run is the store's, so
    every scope granted the dataset lists it."""
    pool = require_pool(request)
    body = await request.json() if await request.body() else {}
    provider = str(body.get("provider") or "").strip()
    collection = str(body.get("collection") or "").strip()
    if not provider or not collection:
        raise HTTPException(status_code=400, detail="provider and collection are required")
    found = find_entry(await _specs_or_404(ctx, provider), kind=CHANGE_CHECK)
    if found is None:
        raise HTTPException(status_code=404, detail=f"{provider!r} declares no change check")
    spec, entry = found
    require_admin_for_provider_job(user, spec)
    if not getattr(user, "is_admin", False):
        # A spec without `asset_provider_id` cannot reach here (provider_specs groups by it), but a
        # change check is never a member's to run either way.
        raise HTTPException(status_code=403, detail=f"{provider!r} change checks are admin operations")
    key = PROVIDER_STORE.prefix()
    settings = await _saved_settings(pool, provider=provider, collection=collection, entry=entry)
    options = await build_options(
        pool, spec, entry, scope=key, provider=provider, collection=collection, settings=settings, validate=False
    )
    # Varies the options hash, like `refresh` on the read path: a check must never answer
    # from an earlier check's cached summary.
    options["requested_at"] = datetime.now(timezone.utc).isoformat()
    run = await start_change_check(
        ctx,
        pool,
        scope_obj=PROVIDER_STORE,
        scope=key,
        provider=provider,
        collection=collection,
        spec=spec,
        entry=entry,
        options=options,
        user=user,
        via="user",
        request=request,
    )
    return JSONResponse(_public_run(run), status_code=202)


@router.get("/scopes/{scope}/asset-changes/runs")
async def api_asset_changes_runs(
    request: Request,
    scope_obj: Scope = Depends(scope_from_path),
    ctx: RestContext = Depends(rest_context),
    provider: str | None = None,
    collection: str | None = None,
    limit: int = 50,
) -> JSONResponse:
    """The change-check runs this scope may read, newest first, finishing any whose job has
    completed: the provider store's runs of every dataset granted to it (attributes or geometry),
    plus its own rows from before the store. The store itself (an admin) reads the store's."""
    pool = require_pool(request)
    limit = max(1, min(int(limit), 200))
    runs = await db_module.list_asset_change_runs(
        pool,
        scope=scope_obj.prefix(),
        provider=provider or None,
        collection=collection or None,
        limit=limit,
    )
    store_runs = await _store_runs_for(pool, scope_obj, provider=provider, collection=collection, limit=limit)
    if store_runs:
        runs = sorted(runs + store_runs, key=lambda r: r.get("created_at") or "", reverse=True)[:limit]
    runs = await finish_pending_runs(ctx, pool, scope_obj, runs)
    return JSONResponse({"runs": [_public_run(r) for r in runs]})


@router.get("/scopes/{scope}/asset-changes/runs/{run_id}/items")
async def api_asset_changes_items(
    run_id: str,
    request: Request,
    scope_obj: Scope = Depends(scope_from_path),
    ctx: RestContext = Depends(rest_context),
) -> JSONResponse:
    """One run's changed nodes (``ada.assets/change-items@1``): one of this scope's own runs, or a
    store run of a dataset granted to it. The item list is read where the RUN's job wrote it."""
    pool = require_pool(request)
    run = await db_module.get_asset_change_run(pool, run_id)
    if run is None or not await _may_read_run(pool, scope_obj, run):
        raise HTTPException(status_code=404, detail="run not found")
    [run] = await finish_pending_runs(ctx, pool, scope_obj, [run])
    items = run.get("items")
    if items is None and run.get("items_key"):
        try:
            doc = json.loads(await ctx.storage.get_bytes(run_scope(run, scope_obj), run["items_key"]))
        except Exception as exc:  # noqa: BLE001 - the run outlived its item file
            raise HTTPException(status_code=410, detail=f"this run's item list is no longer stored: {exc}") from exc
        items = doc.get("items") if isinstance(doc, dict) else doc
    return JSONResponse(
        {"run": _public_run(run), "schema": ITEMS_SCHEMA, "items": items if isinstance(items, list) else []}
    )


# --- Admin → Providers → Scheduled jobs -------------------------------------------------------


@admin_router.get("/asset-schedules")
async def admin_asset_schedules(request: Request, ctx: RestContext = Depends(rest_context)) -> JSONResponse:
    """Everything the Providers tab needs: each provider's declared jobs (with their settings and
    the collections it offers), the frequencies, the schedules made from them with their last
    change-check run, and any plain plugin-job schedule left from before this existed."""
    pool = require_pool(request)
    specs = await provider_specs(ctx)
    providers = []
    for provider, group in sorted(specs.items()):
        spec = group[0]
        providers.append(
            {
                "provider": provider,
                "plugin_id": _plugin_id(spec),
                "collections": collections_of(spec),
                # What an admin may pin per collection (worker-found choices, the worker's default).
                "collection_choices": collection_choice_decls(group),
                "jobs": [
                    {
                        "id": e["id"],
                        "kind": e.get("kind") or "job",
                        "label": e.get("label") or e["id"],
                        "description": e.get("description"),
                        "settings": entry_settings(spec, e),
                        # What the provider calls its collections, plural ("projects"), when it says.
                        "collections_label": e.get("collections_label") or spec.get("asset_collections_label"),
                    }
                    for e in schedule_entries(spec)
                ],
            }
        )
    rows = await db_module.list_plugin_job_schedules(pool)
    asset_rows = [r for r in rows if r.get("asset_provider")]
    # Core's apply-changes cursors, for the rows of that kind: where each collection's next run
    # asks "since" from (no entry: the next run is a baseline).
    cursors_by_provider: dict[str, dict[str, dict]] = {}
    if any(r.get("schedule_kind") == APPLY_CHANGES for r in asset_rows):
        try:
            for c in await db_module.list_asset_apply_cursors(pool):
                cursors_by_provider.setdefault(c["provider"], {})[c["collection"]] = {
                    "cursor": c["cursor"],
                    "applied_at": c["applied_at"],
                    "run_id": c["run_id"],
                }
        except Exception:  # noqa: BLE001 - a listing without cursors beats no listing
            logger.exception("asset schedules: could not read the apply-changes cursors")
    latest = await db_module.latest_asset_change_runs_for_schedules(pool, [r["id"] for r in asset_rows])
    by_cron = {v["cron"]: k for k, v in FREQUENCIES.items()}
    schedules = []
    for row in asset_rows:
        run = latest.get(row["id"])
        if run is not None and run["status"] == "queued":
            # Finished here too, not only by the Sources tab's listing: an admin watching this
            # table after "Run now" must see the result without opening a scope. Read where the
            # run's job wrote (the store, or the scope of a run from before it).
            [run] = await finish_pending_runs(ctx, pool, PROVIDER_STORE, [run])
        found = find_entry(specs.get(row["asset_provider"], []), row["schedule_job"])
        extra: dict = {}
        if row["schedule_kind"] == APPLY_CHANGES:
            held = cursors_by_provider.get(row["asset_provider"], {})
            if row["collection"]:
                one = held.get(str(row["collection"]).lower())
                extra["cursors"] = {str(row["collection"]).lower(): one} if one else {}
            else:
                extra["cursors"] = dict(held)
        schedules.append(
            {
                **extra,
                **{k: row[k] for k in ("id", "collection", "enabled", "last_fired_at", "next_fire_at")},
                # Where it fires, whatever an older row still names: every provider job runs in
                # the store.
                "scope": PROVIDER_STORE_WIRE,
                "provider": row["asset_provider"],
                "job": row["schedule_job"],
                "kind": row["schedule_kind"],
                "frequency": by_cron.get(row["cron_expr"]),
                "settings": settings_of(found[1], row["options"]) if found else {},
                "declared": found is not None,
                "last_skipped_reason": row["last_skipped_reason"],
                "last_job_id": row["last_job_id"],
                "last_run": _public_run(run) if run else None,
            }
        )
    legacy = [
        {k: r[k] for k in ("id", "name", "cron_expr", "scope", "plugin_id", "options", "enabled", "last_fired_at")}
        for r in rows
        if not r.get("asset_provider")
    ]
    return JSONResponse(
        {
            "providers": providers,
            "frequencies": [{"id": k, **v} for k, v in FREQUENCIES.items()],
            "schedules": schedules,
            "legacy": legacy,
        }
    )


@admin_router.get("/asset-schedules/choices")
async def admin_asset_schedule_choices(
    request: Request,
    provider: str,
    job: str,
    scope: str | None = None,
    collection: str = "",
    ctx: RestContext = Depends(rest_context),
) -> JSONResponse:
    """Each setting's choices for one job and collection (``change_users`` comes from the store's
    earlier change checks of that collection). ``scope`` is accepted and ignored: provider jobs
    run in the provider store."""
    pool = require_pool(request)
    found = find_entry(await _specs_or_404(ctx, provider), job)
    if found is None:
        raise HTTPException(status_code=404, detail=f"{provider!r} declares no job {job!r}")
    spec, entry = found
    key = PROVIDER_STORE.prefix()
    return JSONResponse(
        {
            "settings": {
                s["name"]: await setting_choices(pool, s, scope=key, provider=provider, collection=collection)
                for s in entry_settings(spec, entry)
            }
        }
    )


def _frequency_cron(frequency: Any) -> str:
    preset = FREQUENCIES.get(str(frequency or ""))
    if preset is None:
        raise HTTPException(status_code=400, detail=f"frequency must be one of {', '.join(FREQUENCIES)}")
    return preset["cron"]


@admin_router.post("/asset-schedules")
async def admin_asset_schedules_create(
    request: Request,
    user: User = Depends(auth_module.current_user),
    ctx: RestContext = Depends(rest_context),
) -> JSONResponse:
    """Body: ``{provider, job, collection, frequency, settings, enabled}`` -- all choices.

    ``scope`` (from before the provider store) is accepted and ignored: the row stores
    ``providers``, where every provider job runs. One live schedule per (provider, collection,
    job) -- a second, or one beside a row from before the store, is refused (409)."""
    pool = require_pool(request)
    body = await request.json() if await request.body() else {}
    provider = str(body.get("provider") or "").strip()
    job = str(body.get("job") or "").strip()
    collection = str(body.get("collection") or "").strip() or None
    if not provider or not job:
        raise HTTPException(status_code=400, detail="provider and job are required")
    found = find_entry(await _specs_or_404(ctx, provider), job)
    if found is None:
        raise HTTPException(status_code=404, detail=f"{provider!r} declares no job {job!r}")
    spec, entry = found
    if entry.get("kind") == SWEEP:
        collection = None  # every collection, by definition
    cron = _frequency_cron(body.get("frequency"))
    settings = body.get("settings") if isinstance(body.get("settings"), dict) else {}
    options = await build_options(
        pool,
        spec,
        entry,
        scope=PROVIDER_STORE.prefix(),
        provider=provider,
        collection=collection,
        settings=settings,
    )
    taken = "that job is already scheduled for this collection"
    for live in await db_module.list_plugin_job_schedules(pool, asset_provider=provider):
        # Rows from before the store carry their scope in the name, so the name's uniqueness
        # alone would let a second schedule of the same job sit beside one of them.
        if live["schedule_job"] == job and (live["collection"] or None) == collection:
            raise HTTPException(status_code=409, detail=taken)
    name = f"{provider}: {entry.get('label') or job}{' -- ' + collection if collection else ''}"
    try:
        row = await db_module.create_plugin_job_schedule(
            pool,
            name=name,
            cron_expr=cron,
            scope=PROVIDER_STORE_WIRE,
            plugin_id=_plugin_id(spec),
            options=options,
            enabled=bool(body.get("enabled", True)),
            next_fire_at=next_fire(cron),
            created_by=user.sub,
            asset_provider=provider,
            collection=collection,
            schedule_job=job,
            schedule_kind=entry.get("kind") or "job",
        )
    except Exception as exc:
        if exc.__class__.__name__ == "UniqueViolationError":
            raise HTTPException(status_code=409, detail=taken) from exc
        raise
    return JSONResponse(row, status_code=201)


@admin_router.patch("/asset-schedules/{schedule_id}")
async def admin_asset_schedules_update(
    schedule_id: str,
    request: Request,
    ctx: RestContext = Depends(rest_context),
) -> JSONResponse:
    """Body: any of ``{frequency, settings, enabled}``."""
    pool = require_pool(request)
    row = await db_module.get_plugin_job_schedule(pool, schedule_id)
    if row is None or not row.get("asset_provider"):
        raise HTTPException(status_code=404, detail="provider schedule not found")
    body = await request.json() if await request.body() else {}
    fields: dict = {}
    if "frequency" in body:
        fields["cron_expr"] = _frequency_cron(body["frequency"])
        fields["next_fire_at"] = next_fire(fields["cron_expr"])
    if "settings" in body:
        found = find_entry(await _specs_or_404(ctx, row["asset_provider"]), row["schedule_job"])
        if found is None:
            raise HTTPException(status_code=409, detail="the provider no longer declares this job")
        spec, entry = found
        fields["options"] = await build_options(
            pool,
            spec,
            entry,
            scope=PROVIDER_STORE.prefix(),
            provider=row["asset_provider"],
            collection=row["collection"],
            settings=body["settings"] if isinstance(body["settings"], dict) else {},
        )
    if "enabled" in body:
        fields["enabled"] = bool(body["enabled"])
        if fields["enabled"]:
            fields.setdefault("next_fire_at", next_fire(fields.get("cron_expr") or row["cron_expr"]))
    updated = await db_module.update_plugin_job_schedule(pool, schedule_id, **fields)
    return JSONResponse(updated)


@admin_router.delete("/asset-schedules/{schedule_id}")
async def admin_asset_schedules_delete(schedule_id: str, request: Request) -> JSONResponse:
    pool = require_pool(request)
    if not await db_module.archive_plugin_job_schedule(pool, schedule_id):
        raise HTTPException(status_code=404, detail="schedule not found, or already removed")
    return JSONResponse({"archived": schedule_id})


# --- the scheduler's hook --------------------------------------------------------------------


async def asset_schedule_dispatch(ctx: RestContext, pool, schedule_row: dict, options: dict) -> tuple[dict, dict, dict]:
    """For a provider schedule: ``(options, enqueue_kwargs, spec)`` rebuilt from the LIVE
    declaration plus the stored settings, so a provider that changes its fixed options is followed
    without re-saving every schedule. Raises ``LookupError`` with a skip reason when the provider
    is offline or no longer declares the job."""
    provider = schedule_row["asset_provider"]
    specs = (await provider_specs(ctx)).get(provider)
    if not specs:
        raise LookupError(f"no online worker advertises provider {provider!r}; slot skipped")
    found = find_entry(specs, schedule_row["schedule_job"])
    if found is None:
        raise LookupError(f"provider {provider!r} no longer declares job {schedule_row['schedule_job']!r}")
    spec, entry = found
    rebuilt = await build_options(
        pool,
        spec,
        entry,
        # The store, whatever an older row still names: the job fires there.
        scope=PROVIDER_STORE.prefix(),
        provider=provider,
        collection=schedule_row["collection"],
        settings=settings_of(entry, schedule_row["options"] or {}),
        validate=False,
    )
    for key, value in options.items():
        if key not in schedule_row["options"]:
            rebuilt[key] = value  # the scheduler's own stamp (scheduled_at)
    kwargs: dict = {}
    if (entry.get("kind") or "job") == CHANGE_CHECK:
        kwargs["derived_key"], kwargs["derived_prefix"] = job_keys(_plugin_id(spec), rebuilt)
    return rebuilt, kwargs, spec


async def record_scheduled_run(
    pool, schedule_row: dict, *, scope_obj: Scope, job_id: str, derived_key: str | None
) -> None:
    """The run row for a change check the scheduler just fired. Best-effort: the job is already
    queued, and failing to record it must not turn a fired slot into a skipped one.

    Keyed under the provider store -- where ``plugin_schedule_fire`` ran the job, for a row of any
    age -- so its summary is read back from the scope it was written in. ``scope_obj`` is kept for
    callers and ignored for a provider schedule."""
    scope = PROVIDER_STORE.prefix() if schedule_row.get("asset_provider") else scope_obj.prefix()
    try:
        await db_module.insert_asset_change_run(
            pool,
            scope=scope,
            provider=schedule_row["asset_provider"],
            collection=schedule_row["collection"] or "",
            plugin_id=schedule_row["plugin_id"],
            job_id=job_id,
            derived_key=derived_key,
            requested_by="system",
            requested_via="schedule",
            schedule_id=schedule_row["id"],
        )
    except Exception:
        logger.exception("asset schedules: could not record the run of %s", schedule_row.get("id"))
