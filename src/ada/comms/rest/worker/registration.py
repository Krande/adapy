"""What this worker registers on the bus: identity, the capabilities it serves after
qualification, the conversion matrix / utilities / catalog specs it advertises, and the
``publish`` coroutine the job loop re-runs on every heartbeat.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Awaitable, Callable

from ada.config import logger

from ..converter import ConverterRegistry
from ..plugin_registry import locally_registered_specs
from ..qualification import CAPABILITY_REQUIREMENTS_KEY, evaluate
from ..queue import JobQueue, capability_token
from . import state
from .advertise import (
    _advertised_specs,
    _capture_worker_packages,
    _gate_advertised_engines,
)
from .pools import _declared_capabilities, _worker_id


@dataclass
class Registration:
    """Startup verdicts the job loop needs, plus ``publish`` (the registration heartbeat)."""

    worker_id: str
    image_tag: str
    capabilities: list[str]
    withheld: list[dict]
    source_exts: list[str]
    source_ext_set: set[str]
    ext_allow_set: set[str] | None
    conversions: list[dict]
    publish: Callable[[], Awaitable[bool]]


def _connection_specs_for_heartbeat(capabilities: list[str]) -> list[dict]:
    """This worker's registered ``ConnectionSpec``s, in the heartbeat catalog shape Decision 10
    item 4 asks for -- ``all_registered()`` is a process-global registry, so this is exactly what
    a ``clash_check``/``clash_detail`` job running HERE could bind a joint against, told apart
    from the package that registered it by CAPABILITY alone (never by name -- Decision 1's
    convention, reused unchanged).

    A built-in (``ada.clash.builtin_specs.BUILTIN_SPEC_NAMES``) always answers ``capability:
    None``: it runs wherever core runs, no pool required, which is why ``register_builtin_specs``
    is called on EVERY worker here rather than only inside the ``clash_check`` job -- a spec that
    only appeared once a check had already run once would flicker on the panel's first load.
    Anything else answers this worker's own single declared (non-``base``) capability, which is
    the token a ``clash_detail`` job for it would have to be ROUTED to; a worker declaring more
    than one such capability, or none beyond ``base``, cannot be attributed unambiguously and is
    reported as ``None`` (registered here, but this worker cannot say which pool serves it) rather
    than guessed at.
    """
    try:
        from ada.api.connections.spec import all_registered, spec_to_form_schema
        from ada.clash.builtin_specs import BUILTIN_SPEC_NAMES, register_builtin_specs
    except Exception:
        logger.exception("worker: ada.clash unavailable for the connection_specs heartbeat (non-fatal)")
        return []

    try:
        # Tolerant of an already-registered name (see its own docstring) -- called on every
        # heartbeat tick, not once at import time, because a fresh worker process starts with an
        # empty registry and nothing else guarantees the built-ins are in it before this runs.
        register_builtin_specs()
    except Exception:
        logger.exception("worker: register_builtin_specs failed (non-fatal)")

    own = sorted({t for c in capabilities if (t := capability_token(c)) and t != "base"})
    own_capability = own[0] if len(own) == 1 else None

    out: list[dict] = []
    for reg in all_registered():
        spec = reg.spec
        out.append(
            {
                "slug": spec.name,
                "name": spec.name,
                "tags": sorted(spec.tags),
                "priority": spec.priority,
                "roles": spec_to_form_schema(spec),
                "capability": None if spec.name in BUILTIN_SPEC_NAMES else own_capability,
            }
        )
    return out


def _clash_passes_for_heartbeat(capabilities: list[str]) -> list[dict]:
    """This worker's registered clash PASSES, in the same catalog shape as the connection specs.

    A pass is a search a check can run -- core's own look at axes and at surface distances, and a
    plugin may contribute one that works on geometry (mesh interference, with a penetration depth
    and a contact patch). Without this advertisement the panel could only learn of a contributed
    pass by SEEING ONE IN A RESULT, which means it could never be ticked before the first run that
    used it: a checkbox that appears only after you have already managed to do the thing.

    Told apart from the package that registered it by CAPABILITY alone, exactly as a connection
    spec is. Core's passes answer ``capability: None`` -- they run wherever core runs. Anything
    else answers this worker's single declared (non-``base``) capability, which is the token a
    check wanting that pass has to be routed to; a worker declaring several, or none, cannot be
    attributed unambiguously and answers ``None`` rather than a guess.
    """
    try:
        from ada.clash.identify import CORE_PASS_NAMES
        from ada.clash.passes import list_passes
    except Exception:
        logger.exception("worker: ada.clash unavailable for the clash_passes heartbeat (non-fatal)")
        return []

    try:
        # Importing `identify` is what registers core's three passes -- they are registered at
        # import, not at check time, for the same reason `register_builtin_specs` is called here:
        # a pass that only appeared once a check had run would flicker on the panel's first load.
        import ada.clash.identify  # noqa: F401,PLC0415 - imported for its registration side effect
    except Exception:
        logger.exception("worker: core clash passes unavailable for the heartbeat (non-fatal)")

    own = sorted({t for c in capabilities if (t := capability_token(c)) and t != "base"})
    own_capability = own[0] if len(own) == 1 else None

    out: list[dict] = []
    for entry in list_passes():
        out.append(
            {
                "slug": entry["name"],
                "name": entry["name"],
                "label": entry["label"],
                "needs_backend": entry["needs_backend"],
                "priority": entry["priority"],
                # The registry's own attribution wins where it has one -- a plugin that named the
                # pool it needs knows better than this worker does. Otherwise: core's passes are
                # capability-free by definition, and anything else is this worker's, when this
                # worker can be named at all.
                "capability": entry["capability"] or (None if entry["name"] in CORE_PASS_NAMES else own_capability),
            }
        )
    return out


async def build_registration(queue: JobQueue) -> Registration:
    """Evaluate what this worker advertises and serves. Run once at startup, after
    ``ADA_WORKER_PRELOAD`` / plugin discovery populated the registries."""
    image_tag = os.environ.get("ADA_IMAGE_TAG", "").strip()
    # Stash on the module-level slot so ``_audit_done`` can stamp it
    # onto every audit_log row without threading through callers.
    state._WORKER_IMAGE_TAG = image_tag or None
    worker_id = _worker_id()
    capabilities = _declared_capabilities()
    # An extra-capability pool builds FROM / runs an independent adapy and still
    # advertises the full base converter matrix, so it wins base conversion jobs (gxml->glb, ...)
    # it has no business running — and when that image is stale it produces outdated output (e.g.
    # non-manifold meshes). ADA_WORKER_BASE_CONVERSIONS=false makes this worker advertise ZERO base
    # conversions + base source-ext handling, leaving only its capability-routed utilities intact.
    # The clean, version-independent way to scope an extra pool (vs the ADA_WORKER_EXT_ALLOW
    # allowlist, which can only narrow to a positive set of source extensions, not to none).
    base_conversions_enabled = os.environ.get("ADA_WORKER_BASE_CONVERSIONS", "true").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }
    # Source extensions this worker can handle. Pulled from adapy's
    # stream-reader registry — whatever plug-ins ran before this point
    # (e.g. a capability worker's entrypoint that registered an extra
    # format before delegating to ``ada.comms.rest.worker``) has
    # already populated the registry, so we just read what's there.
    # API merges every online worker's list into /api/config so the
    # upload picker stays in sync without anyone having to repeat the
    # suffix list outside the plug-in that owns it.
    from ada.fem.results.artefacts import fea_artefact_extensions

    registered_exts = {e.lower() for e in fea_artefact_extensions()}
    if not base_conversions_enabled:
        # Pool scoped to its own capability only: don't claim any base source-ext
        # handling (FEA bake) — the ext allowlist below is then moot.
        registered_exts = set()
    # Optional per-pod allowlist. Capability (extension-specific) workers
    # build FROM the base image and so inherit its full stream-reader
    # registry — without this gate they'd race the base pool for
    # extensions they don't actually need to handle (e.g. ``.rmed``)
    # and, when running stale code, fail those jobs. The allowlist
    # is comma-separated source suffixes (``.odb,.sqlite``); leading
    # dots optional. Unset → handle everything in the registry, which
    # is the right default for the base worker.
    allow_env = os.environ.get("ADA_WORKER_EXT_ALLOW", "").strip()
    if allow_env:
        ext_allow_set: set[str] | None = {
            ("." + e.strip().lstrip(".")).lower() for e in allow_env.split(",") if e.strip()
        }
        registered_exts &= ext_allow_set
        logger.info(
            "worker: ADA_WORKER_EXT_ALLOW restricts handled exts to %s",
            sorted(ext_allow_set),
        )
    else:
        ext_allow_set = None
    source_exts = sorted(registered_exts)
    # Set form keeps the consume-loop capability check fast — every
    # job lookup needs to hit this; sorting is only for the wire
    # registration above.
    source_ext_set = registered_exts
    started_at = time.time()

    if image_tag:
        try:
            await queue.set_meta("worker_image_tag", image_tag)
            logger.info("worker: published image tag %s", image_tag)
        except Exception:
            logger.exception("worker: failed to publish image tag (non-fatal)")

    # Conversion matrix this worker advertises to the API. Take the
    # full registry (every ``@converter`` registration adapy + any
    # imported plug-in produced) and, if the per-pod allowlist is
    # set, drop entries whose source extension this pod isn't
    # licensed to handle — mirrors the capability gate in the
    # message loop so we don't promise something we'd NAK at
    # delivery time. The API merges every live worker's matrix into
    # ``/api/config["conversionMatrix"]`` for the SPA's /convert page.
    if not base_conversions_enabled:
        # Scoped pool: advertise no base conversions at all (utilities below still register).
        conversions: list[dict] = []
    else:
        full_matrix = ConverterRegistry.matrix()
        if ext_allow_set is not None:
            conversions = [m for m in full_matrix if m["from"] in ext_allow_set]
        else:
            conversions = full_matrix
    # Truthful capability advertisement: restrict the STEP→GLB engine enum to the engines THIS pool
    # can actually run (a slim/adacpp-less pod won't advertise adacpp-native, etc.). The API unions
    # these across pools for the list and routes engine-pinned jobs to a pool that advertises them.
    conversions = _gate_advertised_engines(conversions)

    # Utilities this worker advertises (every ``@utility`` registration adapy +
    # any preloaded plug-in produced). Importing the bundled utilities package
    # registers the built-ins (diff, ...); ADA_WORKER_PRELOAD can add more.
    # Published alongside conversions so the API can merge them into
    # ``/api/config`` for the SPA's Utilities panel.
    try:
        import ada.comms.rest.utilities  # noqa: F401  (registration side-effect)
    except Exception:
        logger.exception("worker: failed to import bundled utilities (non-fatal)")
    from ..utility import UtilityRegistry

    utilities = UtilityRegistry.specs()

    # Catalogs this worker can compile / place / build, advertised (with full
    # catalog-shaped specs) so the viewer's cellbuilder dropdowns union the
    # code-defined defaults with the per-scope DB catalog and with anything a
    # capability worker's ADA_WORKER_PRELOAD registered (register_procedural_*),
    # show each entry's origin, and "sync" a code type into the DB catalog. The
    # API merges them per catalog via ``ada.comms.rest.catalog.merge_catalog_specs``.
    procedural_equipment_types = _advertised_specs(
        "procedural equipment types", "ada.topo_model.equipment", "list_equipment_types"
    )
    procedural_equipment_specs = _advertised_specs(
        "procedural equipment types", "ada.topo_model.equipment", "equipment_archetype_specs"
    )
    procedural_system_types = _advertised_specs("procedural system types", "ada.api.systems", "list_system_types")
    procedural_system_specs = _advertised_specs("procedural system types", "ada.api.systems", "system_type_specs")
    procedural_design_rulesets = _advertised_specs(
        "procedural design rulesets", "ada.topo_model", "design_ruleset_specs"
    )
    # Cell/opening types for the + Cell / + Opening pickers.
    procedural_cell_specs = _advertised_specs(
        "procedural cell/opening types", "ada.topo_model", "procedural_cell_type_specs"
    )
    procedural_opening_specs = _advertised_specs(
        "procedural cell/opening types", "ada.topo_model", "procedural_opening_type_specs"
    )
    # Structural blueprints, advertised PER ENGINE (each spec carries its ``engine``).
    procedural_blueprints = _advertised_specs("procedural blueprints", "ada.topo_model", "procedural_blueprint_specs")
    # Start-from templates for the viewer's "New model from template" dropdown.
    procedural_templates = _advertised_specs("procedural templates", "ada.topo_model", "procedural_template_specs")
    # Per-engine capability flags (e.g. ``supports_grouping``) gating engine-specific UI.
    procedural_engines = _advertised_specs(
        "procedural engine capabilities", "ada.topo_model", "procedural_engine_specs"
    )
    # Detailing engines (a fabrication-detail stage after the structural build).
    procedural_detailing_engines = _advertised_specs("detailing engines", "ada.topo_model", "detailing_engine_specs")
    # Backend plugin specs (the viewer plugin system) for ``/api/plugins``.
    plugin_specs = locally_registered_specs()

    # --- capability qualification ------------------------------------------
    #
    # Advertise a capability only if this environment can be shown to satisfy
    # the requirements declared for it, instead of defending a correctness
    # property with an env var somebody has to remember. See
    # deploy/worker-trust.md §4.
    #
    # EVALUATED ONCE, HERE, and used for BOTH what is advertised and what is
    # subscribed to. Those two must not be able to disagree: an unfit worker
    # that still held a consumer would keep winning jobs with the evidence
    # removed, which is worse than not gating at all.
    #
    # Once at startup, deliberately: a requirement change takes effect when the
    # worker restarts. Re-deciding subscriptions mid-life would mean tearing
    # down consumers under load, and "take this pool out of service NOW" is a
    # different job with a different tool. This gate is for correctness drift,
    # which is a deploy-time property.
    import json as _json

    worker_packages = _capture_worker_packages()
    try:
        _raw_reqs = await queue.get_meta(CAPABILITY_REQUIREMENTS_KEY)
        requirements = _json.loads(_raw_reqs) if _raw_reqs else {}
    except Exception:
        # Being unable to READ the requirements is our plumbing failing, not
        # evidence of unfitness, so it must not take a fleet offline. Fail
        # open — loudly.
        logger.exception("worker: could not read capability requirements; advertising unqualified")
        requirements = {}

    _verdict = evaluate(capabilities, requirements, worker_packages)
    for _w in _verdict.withheld:
        # WARNING: a capability this worker was configured for is not being
        # served. Silence here is the support ticket this design exists to
        # prevent.
        logger.warning("worker: withholding capability %s — %s", _w["capability"], _w["reason"])
    capabilities = _verdict.kept
    withheld = _verdict.withheld

    # Only the packages some requirement actually names. The full manifest is
    # ~24 kB (197 entries, mostly repeated channel URLs) and this row is
    # rewritten on every heartbeat — a recurring cost with no reader. What the
    # row needs to carry is enough for an operator, and the admin panel, to
    # corroborate a withheld reason.
    _named = {
        str(n).lower()
        for e in (requirements or {}).values()
        if isinstance(e, dict)
        for n in list((e.get("requires") or {})) + list((e.get("build_match") or {}))
    }
    reported_packages = [p for p in worker_packages if str(p.get("name") or "").lower() in _named]

    # Connection specs (Decision 10 item 4): a third self-describing advertisement beside
    # `plugin_specs` and the detailing engines above, so a `clash_detail` job's capability
    # routing and the Clashes panel's "which generators exist" listing both come from the same
    # live union — no hardcoded joint-type provider anywhere in core.
    connection_specs = _connection_specs_for_heartbeat(capabilities)
    # Clash passes: which SEARCHES this worker can run, beside which generators it can detail
    # with. Same union, same attribution-by-capability, so the panel can offer a contributed pass
    # as a checkbox BEFORE the first run that uses it.
    clash_passes = _clash_passes_for_heartbeat(capabilities)

    async def _publish_registration() -> bool:
        """Publish the registration; return whether it reached the bus.

        Still non-fatal on its own — one failed heartbeat is a blip, and the
        registry row it writes is decorative. The return value is what lets the
        caller tell a blip from a connection that is never coming back; see
        BUS_HEARTBEAT_FAILURE_LIMIT.
        """
        try:
            await queue.register_worker(
                worker_id,
                {
                    "image_tag": image_tag or None,
                    "capabilities": capabilities,
                    # What this worker declined to serve, and why. Read by the
                    # API so a withheld capability surfaces as unavailable WITH
                    # A REASON rather than simply absent — those two are
                    # indistinguishable to every consumer otherwise.
                    "withheld": withheld,
                    "packages": reported_packages,
                    "source_exts": source_exts,
                    "conversions": conversions,
                    "utilities": utilities,
                    "procedural_equipment_types": procedural_equipment_types,
                    "procedural_equipment_specs": procedural_equipment_specs,
                    "procedural_system_types": procedural_system_types,
                    "procedural_system_specs": procedural_system_specs,
                    "procedural_design_rulesets": procedural_design_rulesets,
                    "procedural_cell_specs": procedural_cell_specs,
                    "procedural_opening_specs": procedural_opening_specs,
                    "procedural_blueprint_specs": procedural_blueprints,
                    "procedural_template_specs": procedural_templates,
                    "procedural_engine_specs": procedural_engines,
                    "procedural_detailing_engine_specs": procedural_detailing_engines,
                    "plugin_specs": plugin_specs,
                    "connection_specs": connection_specs,
                    "clash_passes": clash_passes,
                    "started_at": started_at,
                    "last_heartbeat": time.time(),
                },
            )
        except Exception:
            logger.exception("worker: register_worker failed (non-fatal)")
            return False
        return True

    return Registration(
        worker_id=worker_id,
        image_tag=image_tag,
        capabilities=capabilities,
        withheld=withheld,
        source_exts=source_exts,
        source_ext_set=source_ext_set,
        ext_allow_set=ext_allow_set,
        conversions=conversions,
        publish=_publish_registration,
    )
