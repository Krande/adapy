// Typed client for the hosted viewer's REST API. Every fetch against
// /api/* should go through this module so the URL shape, error
// handling, auth header, and types live in one place.
//
// Pure module — no React, no zustand. Callers compose with stores.

import {runtime} from "@/runtime/config";
import {getAccessToken, isAuthEnabled, refreshAccessToken, signIn} from "@/services/auth/oidc";
import {fetchFeaManifest, fetchResultMeta} from "@/services/feaManifestPoll";
import type {FemConcepts} from "@/extensions/design_and_analysis_extension";

// Known-good target formats keep autocomplete on the call sites that
// hardcode a value (the GLB auto-convert path on upload, etc.), while
// the ``(string & {})`` trailer keeps the type open for whatever new
// targets the worker matrix advertises (.stl, .obj, .step, …) without
// each new pair needing a frontend release.
export type TargetFormat = "glb" | "ifc" | "xml" | (string & {});
export type ConvertStatus = "queued" | "running" | "done" | "error" | "cancelled";

/** Wire-format scope identifier, one of: "shared", "user:me",
 *  "project:<id>". `user:me` is resolved server-side to the caller's
 *  sub so URLs are user-agnostic. */
export type ScopeUrl = string;

/** One successfully renamed/moved source key with its derived-sibling tally. */
export interface MovedKeyEntry {
    old: string;
    new: string;
    siblings_moved: number;
    siblings_failed: string[];
}

export interface MoveKeysResult {
    moved: MovedKeyEntry[];
    failed: Array<{key: string; reason: string}>;
}

/** Group keys under ``oldFolder`` by their parent path relative to it,
 * mapping each group to its ``<newFolder>/<relative_parent>`` move
 * destination. Shared by the user and admin folder rename/move flows —
 * the move endpoint flattens inputs into one target folder, so a single
 * batch call would lose the folder's internal structure. */
function groupKeysByRelativeParent(
    oldFolder: string,
    newFolder: string,
    allKeys: string[],
): Map<string, string[]> {
    const oldTrimmed = oldFolder.replace(/^\/+|\/+$/g, "");
    const newTrimmed = newFolder.replace(/^\/+|\/+$/g, "");
    const prefix = oldTrimmed + "/";
    const groups = new Map<string, string[]>();
    for (const k of allKeys) {
        if (!k.startsWith(prefix)) continue;
        const rest = k.slice(prefix.length);
        const lastSlash = rest.lastIndexOf("/");
        const relParent = lastSlash >= 0 ? rest.slice(0, lastSlash) : "";
        const dest = relParent ? `${newTrimmed}/${relParent}` : newTrimmed;
        if (!groups.has(dest)) groups.set(dest, []);
        groups.get(dest)!.push(k);
    }
    return groups;
}

export interface MeResponse {
    sub: string;
    email: string;
    displayName: string;
    isAdmin: boolean;
    scopes: Array<{kind: "shared" | "user" | "project"; id: string | null; name: string}>;
    projects: Array<{id: string; slug: string; name: string; role: string}>;
}

export interface FileEntry {
    key: string;
    size: number;
}

export interface ConvertResponse {
    job_id: string;
    source_key: string;
    derived_key: string;
    target_format?: TargetFormat;
    status: ConvertStatus;
    progress: number;
    stage: string;
    error: string | null;
    cached: boolean;
    scope_kind?: string;
    scope_id?: string | null;
}

export interface ConvertTargetsResponse {
    source_key: string;
    targets: TargetFormat[];
}

export interface ResultMetaField {
    name: string;
    steps: number[];
}

export interface ResultMeta {
    steps: number[];
    fields: ResultMetaField[];
    default_step: number;
    default_field: string;
}

// ── Component-spec wire types ────────────────────────────────────────
//
// Mirrors ada.api.connections.spec.spec_to_form_schema +
// ada.comms.rest.components_manifest.expose_manifest. Inputs round-
// trip the same dict shape build_component accepts on the backend.

/** One role within a ConnectionSpec — what kind of member fills it,
 *  which sections are allowed, and (when set) which angle constrains
 *  its orientation relative to another role. */
export interface ComponentSpecRoleSchema {
    role: string;
    kind: "BEAM" | "PLATE" | null;
    section_in: string[] | null;
    angle_to_role: string | null;
    angle_range: {min_deg: number; max_deg: number} | null;
    has_predicate: boolean;
}

/** Form-shaped view of a ConnectionSpec. */
export interface ComponentSpecSchema {
    name: string;
    tags: string[];
    priority: number;
    defaults: Record<string, Record<string, unknown>> | null;
    roles: ComponentSpecRoleSchema[];
}

/** One spec entry from the published manifest. ``scope`` records which
 *  scope this entry was discovered in (for routing the build target or
 *  rebuilding the preview URL). ``preview_url`` resolves to a GLB via
 *  the standard /api/scopes/.../blobs route. Counts reflect what the
 *  bake actually produced. */
export interface ComponentSpecManifestEntry {
    scope: string;
    /** Bake branch this manifest was published from — surfaced so the
     *  dropdown can group specs by their lineage. May be absent on
     *  legacy entries published before the field was added. */
    branch?: string;
    /** Worker capability tag responsible for building this spec. The
     *  build POST forwards it verbatim so the backend can route the
     *  job to the matching worker pool. Absent on legacy manifests —
     *  backend then re-resolves it from the manifest top-level. */
    capability?: string;
    schema: ComponentSpecSchema;
    defaults: Record<string, Record<string, unknown>>;
    preview_url: string;
    preview_glb: string;
    tags: string[];
    priority: number;
    beams: number;
    welds: number;
    plates: number;
}

/** Auto-discovered or explicit-scope manifest response. ``sources``
 *  records which scopes contributed entries (one row per scope with a
 *  baked manifest on the requested branch); empty when nothing has
 *  been published anywhere the caller can see. */
export interface ComponentSpecsResponse {
    /** The branch query param echoed back; null when the caller didn't
     *  pin and the server scanned every branch under versions/. */
    branch: string | null;
    sources: Array<{scope: string; branch: string; commit: string}>;
    specs: Record<string, ComponentSpecManifestEntry>;
}

export type ComponentsProfilesResponse =
    | {category: string; profiles: string[]}
    | {categories: string[]};

export interface ComponentBuildPayload {
    spec_name: string;
    /** Same shape as build_sample's `inputs`: per-role keyed by the
     *  lowercase role name, with at minimum a `section` and (when the
     *  role has an angle_range) an `angle_deg`. */
    inputs: Record<string, Record<string, unknown>>;
    /** Optional override for the produced Connection's name. */
    name?: string;
    /** Worker capability tag that should handle this build — usually
     *  the manifest's top-level ``capability`` forwarded verbatim from
     *  the spec entry. When omitted, the backend re-resolves the
     *  scope's manifest to fill it in (built-in adapy specs use the
     *  default pool). */
    capability?: string;
    /** Forwarded to the handler as kwargs. Used by callers that need
     *  to pass handler-specific context (e.g. clash data) from a
     *  downstream consumer. */
    extra_handler_kwargs?: Record<string, unknown>;
}

export interface ComponentBuildResponse {
    job_id: string;
    derived_key: string;
}

// ── Streaming-viewer manifest ────────────────────────────────────────
//
// Backend mirror: ada.fem.results.artefacts.build_manifest. Wire
// shape is locked at version 1 — schema changes bump the version
// field and the client picks a code path off it.

export interface FeaManifestStep {
    /** 0-based index into the field's step stack. */
    i: number;
    /** Time, eigen-frequency, or other monotonic step value. */
    value: number;
    /** Picker display label. */
    label: string;
}

export type FeaScalarRange = {[component: string]: [number, number]};

/** Coarse semantic tag from the bake. Frontend uses this to decide
 *  whether a field drives mesh deformation (only "displacement"
 *  does) and whether the deformation toggle should default on
 *  (everything except "reaction"). Mirrors the backend
 *  FieldCategory Literal type — keep in sync. */
export type FeaFieldCategory =
    | "displacement"
    | "reaction"
    | "stress"
    | "strain"
    | "other";

/** One per (logical-field, element-type) bucket for element fields.
 *  Element fields have an extra axis (integration points) and may
 *  have multiple buckets within a single field — one per element
 *  type the source shipped with. */
export interface FeaManifestFieldPerType {
    /** Adapy-canonical element type ("quad", "triangle", "tetra10", …). */
    elem_type: string;
    n_elements: number;
    n_ips: number;
    /** Optional metadata for the layer / IP pickers. One dict per
     *  integration point, in payload order. Sesam shell fixtures
     *  populate ``layer`` ("top"|"bottom"|"mid") and ``in_plane``
     *  (free-form). Empty when the reader couldn't infer the layout. */
    ip_layout: Array<{ip: number; layer: string; in_plane: string}>;
    /** Element labels in payload order — frontend maps draw-range
     *  labels back to ``element_labels.indexOf(label)`` to find the
     *  row in the AFEL blob. */
    element_labels: number[];
    blob: {
        url: string;
        header_bytes: number;
        stride_bytes: number;
        dtype: string;
        byte_order: "little" | "big";
    };
    /** Per-component min/max within this bucket. The field-level
     *  ``scalar_range`` rolls these up across all buckets. */
    scalar_range: FeaScalarRange;
}

export interface FeaManifestField {
    /** Picker display name; canonicalised across solvers. */
    name_canonical: string;
    /** Solver-native name (e.g. "DEPL", "DISP", "U"). */
    name_native: string;
    /** scalar | vector3 | vector6 | tensor6 | tensor9 | vectorN. */
    kind: string;
    /** Semantic tag set by the reader. Drives the warp-source choice
     *  in the simulation controls. */
    category: FeaFieldCategory;
    support: "nodal" | "element_nodal" | "gauss";
    /** Drives the deformation-scale slider range in the picker:
     * 'static' = [0, 1] (one-directional displacement, signed sweep
     * isn't physical), 'eigen' = [-1, +1] (mode shape has no
     * inherent sign). */
    analysis_kind: "static" | "eigen";
    components: string[];
    /** Nodal fields only — element fields use ``per_type`` instead. */
    blob?: {
        /** Filename relative to the manifest's directory. */
        url: string;
        header_bytes: number;
        stride_bytes: number;
        dtype: string;
        byte_order: "little" | "big";
    };
    /** Element fields only — present iff this field's values live on
     *  integration points (support === "gauss" or "element_nodal").
     *  Nodal fields carry ``blob`` instead. */
    per_type?: FeaManifestFieldPerType[];
    n_steps: number;
    steps: FeaManifestStep[];
    /** Per-component min/max baked at write time so the colormap
     * stays fixed across all steps. Vector fields also carry a
     * "magnitude" entry. */
    scalar_range: FeaScalarRange;
    default_view: {
        reduction: "magnitude" | "scalar" | string;
        colormap: string;
        /** Element fields default to the top layer and ``max_abs``
         *  reduction across IPs. Unused for nodal fields. */
        layer?: string;
        ip_reduction?: string;
    };
}

export interface FeaManifest {
    version: number;
    src: string;
    mesh: {
        url: string;
        n_points: number;
        n_cells: number;
        /** Optional sidecar carrying deduped per-element edge index
         * pairs. When present, the frontend overlays them as a
         * THREE.LineSegments sharing the mesh's position attribute
         * so deformation drives both surface and edges. */
        edges_url?: string;
        n_edges?: number;
        /** Optional AFEM sidecar — per-element (label, tri_start,
         * tri_count). Frontend hydrates these into
         * userdata.id_hierarchy + userdata.draw_ranges_<meshName> so
         * the FEA mesh enters the existing CustomBatchedMesh pick +
         * highlight pipeline. */
        elements_url?: string;
        n_elements?: number;
        /** Optional beam-solid mesh: a parallel GLB carrying every
         *  beam (line) element tessellated as an extruded 3D solid
         *  via OCC. Emitted only when the reader has section + axis
         *  info per beam (SIF today). The companion
         *  ``beam_solids_elements_url`` is an AFEM-format sidecar
         *  keyed by the line-element label, so the frontend can
         *  paint AFEL element fields onto the solid faces with the
         *  same draw-range lookup as the main mesh. */
        beam_solids_url?: string;
        beam_solids_elements_url?: string;
        n_beam_solids?: number;
        /** Optional AFBV sidecar — per-beam-solid-vertex
         *  ``(node0_idx, node1_idx, t)``. The frontend lerps nodal
         *  displacements onto the solid vertices so the solid mesh
         *  deforms in lockstep with its parent beam's endpoints —
         *  without this, large morph-scale factors visually detach
         *  the rigid solid beams from the flexing shells. */
        beam_solids_warp_url?: string;
        n_beam_solid_verts?: number;
        /** Optional AFEG sidecar covering the beam-solid mesh. Same
         *  format as ``edges_url`` but indices reference the
         *  beam-solid vertex buffer. Frontend wires this into a
         *  THREE.LineSegments sharing the beam-solid's position +
         *  morph attributes so the seams between adjacent beam
         *  elements stay visible under deformation. Without this
         *  sidecar the solid beams render as one continuous tube. */
        beam_solids_edges_url?: string;
        n_beam_solid_edges?: number;
    };
    fields: FeaManifestField[];
    /** Optional history-output section (manifest v2+).
     *
     * Field outputs paint values onto the whole mesh; history outputs
     * are a sparse time-series at a hand-picked set of points (the
     * Abaqus *Output, history equivalent / Sesam monitor pts /
     * Code_Aster suivi.). The two have different axes — region ×
     * variable × step × time — so they live in their own section. */
    history?: FeaManifestHistory;
    /** CAD↔FEA lineage stamped by adapy's FEM writer (currently the
     *  code_aster ``<name>.beams.json`` sidecar carries this). The
     *  frontend feeds it to ``useLineageStore`` on load so a click
     *  in this baked FEA model can resolve back to the source CAD
     *  Beam/Plate when the parent assembly's GLB is also loaded. */
    lineage?: FeaManifestLineage;
    /** FEA *input* concepts — point masses, boundary conditions, and
     *  per-case / combination load scenarios — carried from adapy's
     *  deck-write sidecar (the .rmed result file itself holds none of
     *  them). Same shape as the ``fem_concepts`` glTF-extension block,
     *  so the frontend feeds it to ``useFemConceptsStore`` and the
     *  FemConceptsController renders the same masses / BCs / load
     *  overlay it draws for a CAD/FEM GLB's embedded concepts. */
    fem_concepts?: FemConcepts;
    /** FEM node/element sets (design-model meshes). The streaming mesh.glb carries no ADA_EXT,
     *  so the frontend feeds these into useSceneInfoStore for the Scene > FEM groups picker.
     *  Members are tagged EL{id} / P{id} to resolve against the AFEM element ranges. */
    groups?: {name: string; members: string[]; fe_object_type?: "node" | "element"}[];
    legacy_glb?: {url_template: string};
}

export interface FeaManifestLineage {
    /** ``ada.Assembly.guid`` of the source. Matches the
     *  ``assembly_guid`` written into a CAD GLB's ``ADA_EXT_data``
     *  extension when both were exported from the same Assembly. */
    assembly_guid?: string | null;
    /** Dedup table — one entry per unique material referenced by
     *  any group, keyed by material name. Groups reference by
     *  ``material_name``. Optional: an .adapy_fem.json sidecar
     *  predating the bump will simply lack it and the frontend
     *  falls back to a name-only material row. */
    materials?: Record<string, any>;
    /** Same dedup pattern for sections (one per profile, not one
     *  per beam). Groups reference by ``section_name``. */
    sections?: Record<string, any>;
    groups: FeaManifestLineageGroup[];
}

export interface FeaManifestLineageGroup {
    /** Discriminator for the panel's row layout — Beam shows
     *  section + material; Plate shows thickness + material. */
    type?: 'Beam' | 'Plate';
    /** adapy guid of the CAD-side Beam/Plate this group's elements
     *  were meshed from (``FemSection.refs[0].guid``). */
    parent_object_guid: string;
    /** Human-readable CAD-side name, for the panel display when the
     *  parent CAD isn't loaded as an overlay (so we can show the
     *  name without falling back to the guid string). */
    parent_object_name?: string | null;
    /** Beam-only: reference into ``lineage.sections``. */
    section_name?: string | null;
    /** Plate-only: shell section thickness in SI metres. */
    thickness?: number | null;
    /** Reference into ``lineage.materials``. */
    material_name?: string | null;
    /** FEA element labels in this group, prefixed with ``E`` to
     *  match the bake's element-range naming
     *  (load_fea_streaming.ts:183). */
    members: string[];
}

export type FeaHistoryRegionKind = "node" | "element" | "model" | "set";
export type FeaHistoryDomain = "time" | "frequency" | "mode";

export interface FeaHistoryRegion {
    id: string;
    kind: FeaHistoryRegionKind;
    instance: string;
    label: string;
    display_name: string;
    /** (x, y, z) — only present for node regions where the bake could
     *  resolve coordinates from the source mesh. Used for picker
     *  tooltip; absent for element / model / set regions. */
    coords?: [number, number, number];
}

export interface FeaHistoryVariable {
    name_native: string;
    name_canonical: string;
    category: FeaFieldCategory;
    component: string;
    group: string;
    unit: string;
}

export interface FeaHistoryStep {
    i: number;
    name: string;
    procedure: string;
    domain: FeaHistoryDomain;
}

export interface FeaHistorySeries {
    region_id: string;
    /** Native variable name — joins to FeaHistoryVariable.name_native. */
    variable: string;
    /** Index into FeaManifestHistory.steps. */
    step_idx: number;
    times: number[];
    values: number[];
}

export interface FeaManifestHistory {
    regions: FeaHistoryRegion[];
    variables: FeaHistoryVariable[];
    steps: FeaHistoryStep[];
    series: FeaHistorySeries[];
}

class ApiError extends Error {
    constructor(message: string, public status: number, public detail?: string) {
        super(message);
        this.name = "ApiError";
    }
}

async function readDetail(r: Response): Promise<string> {
    try {
        return await r.text();
    } catch {
        return "";
    }
}

async function jsonOrThrow<T>(r: Response, what: string): Promise<T> {
    if (!r.ok) {
        throw new ApiError(`${what} failed: ${r.status} ${r.statusText}`, r.status, await readDetail(r));
    }
    return (await r.json()) as T;
}

function authHeader(): Record<string, string> {
    const t = getAccessToken();
    return t ? {Authorization: `Bearer ${t}`} : {};
}

/**
 * Fetch with auth handling. Attaches the bearer token, and on a 401
 * tries one refresh-then-retry. If still unauthorized, redirects to
 * the IdP — by the time the user comes back, the SPA boots fresh and
 * resumes whatever it was doing.
 *
 * Routes that aren't gated server-side (e.g. /api/config) work
 * regardless because they don't return 401.
 */
async function authedFetch(url: string, init: RequestInit = {}): Promise<Response> {
    const merged: RequestInit = {
        ...init,
        headers: {...(init.headers as Record<string, string> | undefined), ...authHeader()},
    };
    let r = await fetch(url, merged);
    if (r.status === 401 && isAuthEnabled()) {
        const refreshed = await refreshAccessToken();
        if (refreshed) {
            r = await fetch(url, {
                ...init,
                headers: {...(init.headers as Record<string, string> | undefined), ...authHeader()},
            });
            if (r.status !== 401) return r;
        }
        // No path forward — bounce through the IdP. The current URL
        // is preserved as the post-sign-in return target.
        await signIn(window.location.pathname + window.location.search);
        // signIn navigates away, but if it doesn't (popup blocker?),
        // surface the original 401 so callers don't hang.
    }
    return r;
}

export interface AuditEntry {
    id: number;
    ts: string | null;
    user_sub: string | null;
    scope_kind: string;
    scope_id: string | null;
    action: string;
    key: string | null;
    target_format: string | null;
    status: string | null;
    error: string | null;
    duration_ms: number | null;
    traceback: string | null;
    cpu_user_ms: number | null;
    cpu_sys_ms: number | null;
    peak_rss_kb: number | null;
    read_bytes: number | null;
    write_bytes: number | null;
    profile_key: string | null;
    job_id: string | null;
    // M5b: per-row issue-bot sync state for failed user conversions.
    // NULL until the bot has touched the row. Audit-run-attached
    // failures (audit_run_id IS NOT NULL) are processed via the
    // parent run's pass and leave this column NULL by design.
    audit_run_id: string | null;
    issue_bot_status: string | null;
    issue_bot_synced_at: string | null;
    issue_bot_last_error: string | null;
}

// One audit-sweep record. Returned by /admin/audit/runs endpoints.
// Counters are eventually-consistent — total is set once the
// dispatcher finishes enumerating cells; ok/failed/skipped advance
// as worker outcomes land. status flips to 'finished' when their
// sum equals total.
export interface AuditRun {
    id: string;
    scope: string;
    worker_pool: string | null;
    trigger: string;
    started_at: string;
    finished_at: string | null;
    status: string;
    note: string | null;
    total: number;
    ok: number;
    failed: number;
    skipped: number;
    created_by: string | null;
    // M7+: when true, the dispatcher bypassed the cached-blob
    // short-circuit. Useful as a UI badge so an unexpected slow
    // run is recognisable as a perf measurement vs a regression.
    force_rebuild: boolean;
    // M5: issue-bot sync status. NULL until the bot has touched the
    // run; 'syncing' while in flight; terminal 'done'/'skipped'/'failed'.
    issue_bot_status: string | null;
    issue_bot_last_error: string | null;
    issue_bot_synced_at: string | null;
}

// Per-deployment configuration for the audit-failure → issue tracker
// bridge. ``token_env_name`` references the env var that carries the
// token (sourced from a k8s Secret); ``token_present`` reflects
// whether that env var is set on the serving API replica.
export interface IssueTargetConfig {
    kind: "disabled" | "github" | "forgejo";
    repo: string;
    base_url: string;
    token_env_name: string;
    token_present: boolean;
}

// One row in the cross-conversion perf table (M6). ``streaming``
// is the classifier's verdict; ``signals`` lists the threshold keys
// that fired so the UI can render specific reasons in a tooltip.
export interface PerfCell {
    source_ext: string;
    target_format: string;
    sample_count: number;
    fail_count: number;
    ok_count: number;
    failure_rate: number;
    duration_ms_p50: number | null;
    duration_ms_p95: number | null;
    duration_ms_max: number | null;
    peak_rss_kb_p50: number | null;
    peak_rss_kb_p95: number | null;
    peak_rss_max_kb: number | null;
    peak_rss_per_source_mb_p95: number | null;
    write_bytes_p50: number | null;
    write_bytes_p95: number | null;
    read_bytes_avg: number | null;
    // Fraction of wall-clock spent in CPU (user + sys) across all
    // samples. Null when no rows had non-null duration. Below the
    // ``cpu_fraction_max`` threshold the classifier flags the cell
    // as IO-bound — see ``streaming.signals`` for the firing list.
    cpu_fraction: number | null;
    streaming: {is_candidate: boolean; signals: string[]};
}

// One aggregated hot function inside a cell. ``agg_cumtime`` is the
// SUM of pstats' ``cumtime`` across every profiled run in the
// window — total seconds the function and its callees consumed.
export interface PerfHotspotRow {
    func: string;
    file: string;
    line: number;
    agg_cumtime: number;
    agg_ncalls: number;
    profiles_seen: number;
}

export interface PerfHotspotsResp {
    source_ext: string | null;
    target_format: string | null;
    functions: PerfHotspotRow[];
    profiles_in_window: number;
    total_top_cumtime_in_window: number;
    since_days: number;
}

export interface PerfReport {
    cells: PerfCell[];
    thresholds: Record<string, number>;
    signal_reasons: Record<string, string>;
    since_days: number;
    trigger: "all" | "audit" | "user";
    audit_run_id: string | null;
    worker_image_tag: string | null;
    generated_at: string;
}

export interface PerfThresholdsResp {
    thresholds: Record<string, number>;
    defaults: Record<string, number>;
}

// One audit_log row scoped to a parent audit_run. Narrower projection
// than ``AuditEntry`` — the grid view doesn't need user_sub /
// scope_kind / traceback (all redundant for cells in one run).
export interface AuditRunJob {
    id: number;
    ts: string | null;
    key: string | null;
    target_format: string | null;
    status: string | null;
    error: string | null;
    duration_ms: number | null;
    cpu_user_ms: number | null;
    cpu_sys_ms: number | null;
    peak_rss_kb: number | null;
    read_bytes: number | null;
    write_bytes: number | null;
    job_id: string | null;
    // Image tag of the worker pod that processed this cell.
    // Empty for cells finished before migration 013 / cells that
    // hit the dispatcher's cached short-circuit.
    worker_image_tag: string | null;
}

export interface ProfileStatsRow {
    func: string;
    file: string;
    line: number;
    ncalls: number;
    primitive_calls: number;
    tottime: number;
    percall_tot: number;
    cumtime: number;
    percall_cum: number;
}

export interface ProfileStatsResp {
    audit_id: number;
    total_tottime: number;
    row_count: number;
    rows: ProfileStatsRow[];
}

export interface MetricsSample {
    ts: number;            // epoch seconds
    elapsed_s: number;     // seconds since job start
    cpu_user_ms: number;
    cpu_sys_ms: number;
    rss_kb: number;
    peak_rss_kb: number;
    read_bytes: number;
    write_bytes: number;
}

export interface MetricsHistoryResp {
    audit_id: number;
    samples: MetricsSample[];
}

export interface AdminProject {
    id: string;
    slug: string;
    name: string;
    created_at: string | null;
    archived_at: string | null;
    member_count: number;
}

export interface ProjectMember {
    user_sub: string;
    role: string;
    added_at: string | null;
    email: string | null;
    display_name: string | null;
    last_seen_at: string | null;
}

// One admin-curated regression corpus. Returned by /admin/corpora
// endpoints. Wire format on its scope is ``corpus:<slug>``; the
// storage layer uses ``corpus/<slug>/`` as the bucket prefix.
export interface Corpus {
    id: string;
    slug: string;
    name: string;
    description: string | null;
    created_at: string | null;
    created_by: string | null;
    archived_at: string | null;
}

// One recurring audit schedule. The API scheduler tick fires the
// row's (scope, worker_pool) sweep every ``cron_expr`` slot. The UI
// shows ``next_fire_at`` so admins know when the next run lands;
// ``last_skipped_reason`` surfaces when a tick decided not to
// dispatch (e.g. concurrent-fire guard).
export interface AuditSchedule {
    id: string;
    name: string;
    cron_expr: string;
    scope: string;
    worker_pool: string | null;
    enabled: boolean;
    last_fired_at: string | null;
    next_fire_at: string | null;
    last_skipped_reason: string | null;
    created_at: string | null;
    created_by: string | null;
    archived_at: string | null;
}

export interface AuditFilters {
    user_sub?: string;
    scope_kind?: string;
    scope_id?: string;
    action?: string;
    before_id?: number;
    limit?: number;
}

export interface DerivedBlob {
    format: string;
    key: string;
    size: number;
    last_modified: string | null;
}

export interface AdminFileEntry {
    key: string;
    size: number;
    last_modified: string | null;
    format: string;
    available_targets: TargetFormat[];
    derived: DerivedBlob[];
    orphan?: boolean;
}

/** Per-scope state of a compression-sweep background task. */
export interface CompressionSweepState {
    started_at: number;
    completed_at: number | null;
    last_update: number;
    total: number;
    processed: number;
    compressed: number;
    already_gzipped: number;
    bytes_before: number;
    bytes_after: number;
    errors: {key: string; error: string}[];
    error: string | null;
    cancelled: boolean;
    /** Filename currently being compressed, if any. */
    current_key: string | null;
    /** Server marks ``true`` when ``completed_at`` is null and the
     * ``last_update`` heartbeat is older than 90 s — most likely the
     * viewer pod restarted mid-sweep and the BackgroundTask was lost. */
    orphaned: boolean;
}

/** One worker pod's self-reported registration entry. */
export interface WorkerEntry {
    worker_id: string;
    image_tag: string | null;
    capabilities: string[];
    started_at: number;
    last_heartbeat: number;
    online: boolean;
}

export const viewerApi = {
    /** Direct URL for the addressable blob endpoint. Includes scope.
     * Only safe to use as `<a href download>` when auth is disabled —
     * with auth on use :func:`downloadBlob` so the bearer token rides
     * along. */
    blobUrl(scope: ScopeUrl, key: string): string {
        return `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/blobs/${encodeURIComponent(key)}`;
    },

    /** Bootstrap the SPA's identity + available scopes. */
    async me(): Promise<MeResponse> {
        const r = await authedFetch(`${runtime.apiBase()}/me`);
        return jsonOrThrow<MeResponse>(r, "me");
    },

    async listFiles(scope: ScopeUrl): Promise<FileEntry[]> {
        const r = await authedFetch(
            `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/files`,
        );
        const body = await jsonOrThrow<{files: FileEntry[]}>(r, `listFiles(${scope})`);
        return body.files;
    },

    /** Same scope file listing as ``listFiles`` but with each source's
     * existing derived blobs grouped under it. The /convert page uses
     * this to show pre-existing conversions next to fresh upload rows
     * — the user wants to spot "I already converted this last week,
     * just give me the GLB" without re-running the converter. Server
     * filters orphan derived (no matching source in this scope); use
     * the admin storage list for cleanup. */
    async listFilesWithDerived(scope: ScopeUrl): Promise<AdminFileEntry[]> {
        const r = await authedFetch(
            `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/files?include_derived=1`,
        );
        const body = await jsonOrThrow<{files: AdminFileEntry[]}>(
            r, `listFilesWithDerived(${scope})`,
        );
        return body.files;
    },

    /** Trigger a browser download of a stored blob. Fetches with auth,
     * materialises a blob: URL, clicks a hidden anchor, then revokes
     * the URL to release memory. Works in both auth-on and auth-off
     * modes — the only cost over `<a href>` is one extra round-trip
     * the browser would have made anyway. */
    async downloadBlob(scope: ScopeUrl, key: string, suggestedName: string): Promise<void> {
        const r = await authedFetch(this.blobUrl(scope, key));
        if (!r.ok) {
            throw new ApiError(`downloadBlob(${key})`, r.status, await readDetail(r));
        }
        const blob = await r.blob();
        const url = URL.createObjectURL(blob);
        try {
            const a = document.createElement("a");
            a.href = url;
            a.download = suggestedName;
            a.style.display = "none";
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
        } finally {
            URL.revokeObjectURL(url);
        }
    },

    /** Delete an own file (derived blobs cascade server-side).
     * Personal scope only — shared/project scopes return 403; admins
     * use adminDeleteBlob there. */
    async deleteBlob(
        scope: ScopeUrl,
        key: string,
    ): Promise<{deleted: string[]; errors?: string[]}> {
        const r = await authedFetch(this.blobUrl(scope, key), {method: "DELETE"});
        return jsonOrThrow(r, "deleteBlob");
    },

    /** Batch-move own source keys into a destination folder. Personal
     * scope only; mirrors adminMoveKeysToFolder. */
    async moveKeysToFolder(
        scope: ScopeUrl,
        keys: string[],
        folder: string,
    ): Promise<MoveKeysResult> {
        const r = await authedFetch(
            `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/keys/move-to-folder`,
            {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({keys, folder}),
            },
        );
        return jsonOrThrow(r, "moveKeysToFolder");
    },

    /** Rename a single own source key (derived blobs follow). Personal
     * scope only. 409 → target exists, 404 → source missing. */
    async renameKey(
        scope: ScopeUrl,
        oldKey: string,
        newKey: string,
    ): Promise<MovedKeyEntry> {
        const r = await authedFetch(
            `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/keys/rename`,
            {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({old_key: oldKey, new_key: newKey}),
            },
        );
        return jsonOrThrow(r, "renameKey");
    },

    /** Rename or relocate a folder prefix in the personal scope —
     * user-level twin of adminRenameOrMoveFolder (same grouped-move
     * strategy, see that method's docstring). */
    async renameOrMoveFolder(
        scope: ScopeUrl,
        oldFolder: string,
        newFolder: string,
        allKeys: string[],
    ): Promise<MoveKeysResult> {
        const groups = groupKeysByRelativeParent(oldFolder, newFolder, allKeys);
        const movedAll: MovedKeyEntry[] = [];
        const failedAll: Array<{key: string; reason: string}> = [];
        // Sequential not parallel: each call mutates the scope's keyset
        // on the server; concurrent calls would race on collision
        // detection.
        for (const [dest, keys] of groups) {
            const r = await this.moveKeysToFolder(scope, keys, dest);
            movedAll.push(...r.moved);
            failedAll.push(...r.failed);
        }
        return {moved: movedAll, failed: failedAll};
    },

    /** Fetch raw bytes for a key. Used by the in-browser Pyodide
     * pipeline to read its source from storage. */
    async getBlob(scope: ScopeUrl, key: string): Promise<ArrayBuffer> {
        const r = await authedFetch(this.blobUrl(scope, key));
        if (!r.ok) {
            throw new ApiError(`getBlob(${key})`, r.status, await readDetail(r));
        }
        return await r.arrayBuffer();
    },

    /** Upload bytes under a given key. body is anything fetch/XHR can
     * send (File, Blob, ArrayBuffer, ...). When `onProgress` is given,
     * the request goes through XMLHttpRequest because fetch doesn't
     * expose upload progress consistently across browsers. */
    async putBlob(
        scope: ScopeUrl,
        key: string,
        body: BodyInit,
        opts?: {onProgress?: (loaded: number, total: number) => void},
    ): Promise<void> {
        if (!opts?.onProgress) {
            const r = await authedFetch(this.blobUrl(scope, key), {
                method: "PUT",
                body,
                headers: {"Content-Type": "application/octet-stream"},
            });
            if (!r.ok) {
                throw new ApiError(`putBlob(${key})`, r.status, await readDetail(r));
            }
            return;
        }

        // Progress-tracked path uses XHR. authedFetch's refresh-then-
        // retry pattern is open-coded here so the upload survives a
        // token expiring just before the PUT lands — observed when a
        // user picks a large file after a long idle.
        const fireUpload = (): Promise<void> =>
            new Promise<void>((resolve, reject) => {
                const xhr = new XMLHttpRequest();
                xhr.open("PUT", this.blobUrl(scope, key));
                xhr.setRequestHeader("Content-Type", "application/octet-stream");
                const t = getAccessToken();
                if (t) xhr.setRequestHeader("Authorization", `Bearer ${t}`);
                xhr.upload.addEventListener("progress", (e) => {
                    if (e.lengthComputable) {
                        opts.onProgress!(e.loaded, e.total);
                    }
                });
                xhr.addEventListener("load", () => {
                    if (xhr.status >= 200 && xhr.status < 300) {
                        resolve();
                    } else {
                        reject(
                            new ApiError(
                                `putBlob(${key}) failed: ${xhr.status}`,
                                xhr.status,
                                xhr.responseText || "",
                            ),
                        );
                    }
                });
                xhr.addEventListener("error", () =>
                    reject(new ApiError(`putBlob(${key}) network error`, 0, "")),
                );
                xhr.addEventListener("abort", () =>
                    reject(new ApiError(`putBlob(${key}) aborted`, 0, "")),
                );
                xhr.send(body as XMLHttpRequestBodyInit);
            });

        // Pre-flight: if our cached token has fallen out of the 30s
        // skew window, refresh before we start the (potentially slow)
        // upload so the body isn't sent with no Authorization header.
        if (!getAccessToken()) {
            await refreshAccessToken();
        }
        try {
            await fireUpload();
        } catch (e) {
            if (!(e instanceof ApiError) || e.status !== 401) throw e;
            const refreshed = await refreshAccessToken();
            if (!refreshed) throw e;
            await fireUpload();
        }
    },

    /** Upload a pyodide-derived blob (e.g. an in-browser GLB conversion
     * of a STEP/IFC source) and return the canonical derived key the
     * server stored it under. Wraps PUT /api/scopes/{scope}/derived,
     * which computes the key from (source, target) so the SPA doesn't
     * need to mirror the server's naming convention. */
    async putDerivedBlob(
        scope: ScopeUrl,
        sourceKey: string,
        target: TargetFormat,
        body: BodyInit,
    ): Promise<string> {
        // managed_audit=1: the WASM pipeline records its own metrics-rich
        // audit row via auditLocalCreate/Update, so tell the derived-PUT
        // not to also auto-audit (which would double-count the conversion).
        const url =
            `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/derived` +
            `?source=${encodeURIComponent(sourceKey)}&target=${encodeURIComponent(target)}&managed_audit=1`;
        const r = await authedFetch(url, {
            method: "PUT",
            body,
            headers: {"Content-Type": "application/octet-stream"},
        });
        if (!r.ok) {
            throw new ApiError(`putDerivedBlob(${sourceKey})`, r.status, await readDetail(r));
        }
        const j: {key: string; size: number} = await r.json();
        return j.key;
    },

    /** Open an audit row for an in-browser (WASM) conversion. Returns the
     * server-assigned ``wasm-<uuid>`` job id to pass to auditLocalUpdate.
     * ``auditRunId`` attaches the row to an admin audit-run sweep. */
    async auditLocalCreate(
        scope: ScopeUrl,
        body: {
            key: string;
            target_format: string;
            audit_run_id?: string | null;
            image_tag?: string;
        },
    ): Promise<string> {
        const r = await authedFetch(
            `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/audit/local`,
            {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify(body),
            },
        );
        const j = await jsonOrThrow<{job_id: string}>(r, "auditLocalCreate");
        return j.job_id;
    },

    /** Patch a WASM conversion's audit row to its terminal outcome with
     * captured metrics. Best-effort at the call site — a lost audit
     * update must never fail the conversion. */
    async auditLocalUpdate(
        scope: ScopeUrl,
        jobId: string,
        body: {
            status: "done" | "ok" | "error" | "skipped" | "cancelled";
            duration_ms?: number;
            read_bytes?: number;
            write_bytes?: number;
            peak_rss_kb?: number;
            error?: string | null;
            traceback?: string | null;
            metrics_samples?: Array<Record<string, number>>;
        },
    ): Promise<void> {
        const r = await authedFetch(
            `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/audit/local/${encodeURIComponent(jobId)}`,
            {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify(body),
            },
        );
        if (!r.ok) {
            throw new ApiError(`auditLocalUpdate(${jobId})`, r.status, await readDetail(r));
        }
    },

    /** Upload a browser-baked FEA artefact tree (a zip of fea.manifest.json
     * + fea.mesh.glb + fea.*.bin) produced by the in-browser FEM stack. The
     * server unpacks it under ``_derived/<source>.fea/`` with the worker's
     * gzip policy. Returns the manifest key. */
    async uploadFeaArtefacts(scope: ScopeUrl, sourceKey: string, zip: BodyInit): Promise<string> {
        const url =
            `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/fea/artefacts` +
            `?source=${encodeURIComponent(sourceKey)}`;
        const r = await authedFetch(url, {
            method: "POST",
            body: zip,
            headers: {"Content-Type": "application/zip"},
        });
        const j = await jsonOrThrow<{manifest_key: string; count: number}>(r, "uploadFeaArtefacts");
        return j.manifest_key;
    },

    /** Request a presigned PUT URL for a too-large-to-buffer upload.
     *
     * Used by uploadFile when the file exceeds the server's regular
     * upload cap (~200 MB). Server returns a one-shot URL the browser
     * PUTs the raw bytes to directly. Local-backed deployments 503
     * here — operator must run with an S3-compatible backend. */
    async requestUploadUrl(
        scope: ScopeUrl,
        key: string,
    ): Promise<{
        url: string;
        key: string;
        method: string;
        expires_in_seconds: number;
        /** Server hint: when set, the client should compress the body
         * with this encoding and send Content-Encoding: <value> on the
         * PUT. The encoding header is *not* signed into the URL — sent
         * as opaque metadata — so a client lacking CompressionStream
         * can ignore it and PUT raw bytes; the sweep job will pick it
         * up later. */
        content_encoding?: string | null;
    }> {
        const r = await authedFetch(
            `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/upload-url`,
            {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({key}),
            },
        );
        return jsonOrThrow(r, `requestUploadUrl(${key})`);
    },

    /** Finalise a presigned-URL upload: server confirms the object
     * landed and writes the audit row. Caller should run this only
     * after a successful direct PUT — otherwise it 404s. */
    async completeUpload(scope: ScopeUrl, key: string): Promise<{key: string; size: number}> {
        const r = await authedFetch(
            `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/upload-complete`,
            {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({key}),
            },
        );
        return jsonOrThrow(r, `completeUpload(${key})`);
    },

    /** Inventory of (steps, fields) for a FEA result file.
     *
     * Cache hit: returns the parsed inventory immediately.
     * Cache miss: server enqueues a worker SIF parse and returns 202;
     * this client polls /api/convert/{job_id} until done, then
     * re-fetches the endpoint and returns the parsed body.
     *
     * Orchestration lives in feaManifestPoll.ts so tests can drive
     * mock fetchers + clocks without spinning up React.
     *
     * 415 if the source isn't a result file; 422 if it is but has
     * no usable result data. */
    async resultMeta(scope: ScopeUrl, sourceKey: string): Promise<ResultMeta> {
        return fetchResultMeta({
            fetcher: authedFetch,
            convertStatus: (jobId) => this.convertStatus(jobId),
            apiBase: runtime.apiBase(),
            scope,
            sourceKey,
        });
    },

    /** Streaming-viewer manifest for a FEA source (.rmed or .sif).
     *
     * Cache hit: returns the manifest immediately.
     * Cache miss: server enqueues a worker bake job and returns 202.
     * This client polls /api/convert/{job_id} until the job hits
     * status=done (or error), then re-fetches the manifest endpoint
     * and returns the body.
     *
     * The bake runs in the worker container — the slim API container
     * doesn't carry the ada.fem deps that h5py / trimesh / RMED parse
     * need. Frontend doesn't see that detail; it just polls.
     *
     * 415 on unsupported source extensions, 404 on missing source.
     * Throws on bake error. */
    async feaManifest(
        scope: ScopeUrl,
        sourceKey: string,
        opts?: {
            onProgress?: (info: {
                jobId: string;
                stage: string;
                progress: number;
                status: "queued" | "running" | "done";
            }) => void;
            signal?: AbortSignal;
        },
    ): Promise<FeaManifest> {
        return fetchFeaManifest({
            fetcher: authedFetch,
            convertStatus: (jobId) => this.convertStatus(jobId),
            apiBase: runtime.apiBase(),
            scope,
            sourceKey,
            signal: opts?.signal,
            onProgress: opts?.onProgress,
        });
    },

    /** Compose the full URL of a FEA artefact blob (mesh GLB or
     * field blob) under the existing /blobs/{key} route. The
     * manifest carries plain filenames; this helper makes them
     * absolute with the right scope + source-prefix shape so callers
     * don't have to re-encode the convention. */
    feaArtefactBlobUrl(
        scope: ScopeUrl,
        sourceKey: string,
        filename: string,
    ): string {
        const cleanSrc = sourceKey.replace(/^\/+/, "");
        const cleanFile = filename.replace(/^\/+/, "");
        return (
            `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/blobs/` +
            `_derived/${cleanSrc}.fea/${cleanFile}`
        );
    },

    /** Enqueue a server-side conversion. Returns either a fresh queued
     * job, a synthesised "cached" response (derived already present),
     * or rejects with ApiError. ``step`` and ``field`` only apply to
     * FEA result sources (.sif) — set both to override the default
     * field selection, or leave both undefined for the auto pick. */
    async convert(
        scope: ScopeUrl,
        sourceKey: string,
        targetFormat: TargetFormat = "glb",
        opts?: {
            step?: number;
            field?: string;
            // Per-job knobs. Keys come from the conversion matrix's
            // ``options[<target>]`` schema (declared at the worker
            // ``@converter(options=...)`` site) plus the legacy
            // hardcoded set (use_sat_pcurves / pcurve_drive_edge /
            // skip_shapefix / profile_conversions) that still ride
            // the env-var rail. Values are tri-state native:
            // ``null`` clears any global override; otherwise the
            // type matches the option's declared ``type``.
            conversionOptions?: Record<string, boolean | string | number | null>;
        },
    ): Promise<ConvertResponse> {
        const body: Record<string, unknown> = {
            source_key: sourceKey,
            target_format: targetFormat,
        };
        if (opts?.step !== undefined && opts?.field !== undefined) {
            body.step = opts.step;
            body.field = opts.field;
        }
        if (opts?.conversionOptions && Object.keys(opts.conversionOptions).length) {
            body.conversion_options = opts.conversionOptions;
        }
        const r = await authedFetch(
            `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/convert`,
            {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify(body),
            },
        );
        return jsonOrThrow<ConvertResponse>(r, `convert(${sourceKey} -> ${targetFormat})`);
    },

    /** Poll a single conversion job by id. Job_id is globally unique,
     * so the URL doesn't carry a scope — the server re-checks access
     * against the scope recorded on the job. */
    async convertStatus(jobId: string): Promise<ConvertResponse> {
        const r = await authedFetch(`${runtime.apiBase()}/convert/${encodeURIComponent(jobId)}`);
        return jsonOrThrow<ConvertResponse>(r, `convertStatus(${jobId})`);
    },

    /** Enqueue a worker utility against a loaded scene model. Returns the job
     * (poll via ``convertStatus``; on ``done`` fetch ``derived_key`` for the
     * viewer-ops JSON). Mirrors :func:`convert` but hits the utility endpoint. */
    async runUtility(
        scope: ScopeUrl,
        sourceKey: string,
        utilityName: string,
        kwargs: Record<string, boolean | string | number | null>,
    ): Promise<ConvertResponse> {
        const r = await authedFetch(
            `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/utility`,
            {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({source_key: sourceKey, utility_name: utilityName, kwargs}),
            },
        );
        return jsonOrThrow<ConvertResponse>(r, `runUtility(${utilityName} on ${sourceKey})`);
    },

    // ── Connection-component panel ───────────────────────────────────
    //
    // Backed by /api/components/{profiles,specs,build}; build status
    // polling reuses convertStatus since component_build jobs flow
    // through the same NATS queue + KV.

    /** Discover published component-spec libraries.
     *
     * Default (no `scope` arg): server scans every scope the caller
     * can access (personal + shared + project memberships) and
     * aggregates whichever have a manifest. Each entry carries the
     * `scope` it was found in. Explicit `scope` restricts to that one
     * scope.
     *
     * Bakes are published per-commit by ada-build's run-and-upload
     * entrypoint; the server resolves "latest on branch" per scope
     * and exposes `preview_url` for each spec pointing at the sibling
     * GLB. Empty `specs` when nothing's been published anywhere the
     * caller can see. */
    async componentsSpecs(opts?: {scope?: ScopeUrl; branch?: string}): Promise<ComponentSpecsResponse> {
        const params = new URLSearchParams();
        if (opts?.scope) params.set("scope", opts.scope);
        if (opts?.branch) params.set("branch", opts.branch);
        const q = params.toString();
        const r = await authedFetch(
            `${runtime.apiBase()}/components/specs${q ? `?${q}` : ""}`,
        );
        return jsonOrThrow<ComponentSpecsResponse>(r, `componentsSpecs`);
    },

    /** Section catalog for a SectionCat category (e.g. "iprofiles" →
     * ["HEA100", ...]). Empty list for categories without ProfileDB
     * coverage today (BOX/SHS); the form falls back to free-text
     * input for those. With no `category`, returns the catalog of
     * supported category names. */
    async componentsProfiles(category?: string): Promise<ComponentsProfilesResponse> {
        const url = category
            ? `${runtime.apiBase()}/components/profiles?category=${encodeURIComponent(category)}`
            : `${runtime.apiBase()}/components/profiles`;
        const r = await authedFetch(url);
        return jsonOrThrow<ComponentsProfilesResponse>(r, `componentsProfiles(${category ?? ""})`);
    },

    /** Enqueue an on-demand component build for user-tweaked inputs.
     * Returns `{job_id, derived_key}`; poll status via convertStatus
     * and fetch the result GLB via getBlob(scope, derived_key) once
     * the job reports `done`. */
    async componentsBuild(
        payload: ComponentBuildPayload,
        opts?: {scope?: ScopeUrl},
    ): Promise<ComponentBuildResponse> {
        const url = opts?.scope
            ? `${runtime.apiBase()}/components/build?scope=${encodeURIComponent(opts.scope)}`
            : `${runtime.apiBase()}/components/build`;
        const r = await authedFetch(url, {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify(payload),
        });
        return jsonOrThrow<ComponentBuildResponse>(r, `componentsBuild(${payload.spec_name})`);
    },

    /** In-flight conversions the current user started in this scope.
     *  Used by the bottom-right toast to repopulate on page reload so
     *  a long bake the user kicked off and walked away from still
     *  shows up when they come back. Errors are intentionally
     *  excluded — they're terminal and the toast's error row expects
     *  manual dismissal, not silent restore. */
    async myJobs(scope: ScopeUrl, limit = 200): Promise<AuditEntry[]> {
        const r = await authedFetch(
            `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/my-jobs` +
            `?limit=${encodeURIComponent(String(limit))}`,
        );
        const body = await jsonOrThrow<{jobs: AuditEntry[]}>(r, `myJobs(${scope})`);
        return body.jobs;
    },

    /** Cancel an in-flight conversion the current user owns. Returns
     *  true on success, false if the row was missing / not owned /
     *  already terminal. Worker isn't notified — the bake will keep
     *  going to completion in the background but its audit row is
     *  marked cancelled and disappears from the toast. */
    async cancelMyJob(scope: ScopeUrl, jobId: string): Promise<boolean> {
        const r = await authedFetch(
            `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}` +
            `/my-jobs/${encodeURIComponent(jobId)}/cancel`,
            {method: "POST"},
        );
        if (r.status === 404) return false;
        await jsonOrThrow<{job_id: string; cancelled: boolean}>(
            r, `cancelMyJob(${jobId})`,
        );
        return true;
    },

    /** Server-side viable-target listing. The frontend mirrors this
     * mapping client-side too, but this lets us cross-check. */
    async convertTargets(scope: ScopeUrl, sourceKey: string): Promise<TargetFormat[]> {
        const url =
            `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}` +
            `/convert/targets?source_key=${encodeURIComponent(sourceKey)}`;
        const r = await authedFetch(url);
        if (!r.ok) return [];
        const body = (await r.json()) as ConvertTargetsResponse;
        return body.targets || [];
    },

    /** Admin: paged audit log. ``before_id`` is the keyset cursor —
     * pass ``next_before_id`` from the previous page to get the next
     * older one. Returns null for ``next_before_id`` when at the end. */
    async adminAudit(
        filters: AuditFilters = {},
    ): Promise<{entries: AuditEntry[]; next_before_id: number | null}> {
        const params = new URLSearchParams();
        for (const [k, v] of Object.entries(filters)) {
            if (v !== undefined && v !== "" && v !== null) params.set(k, String(v));
        }
        const qs = params.toString();
        const url = `${runtime.apiBase()}/admin/audit${qs ? `?${qs}` : ""}`;
        const r = await authedFetch(url);
        return jsonOrThrow(r, "adminAudit");
    },

    /** Admin: list live regression corpora (admin-curated proprietary
     * source sets driving M3 audit sweeps). Archived rows hidden. */
    async adminCorporaList(): Promise<{corpora: Corpus[]}> {
        const r = await authedFetch(`${runtime.apiBase()}/admin/corpora`);
        return jsonOrThrow(r, "adminCorporaList");
    },

    /** Admin: create a new corpus. ``slug`` is the human-readable id
     * embedded in scope tokens (``corpus:<slug>``). Storage prefix +
     * wire format both follow from it; 409 on duplicate live slug. */
    async adminCorpusCreate(
        body: {slug: string; name: string; description?: string | null},
    ): Promise<Corpus> {
        const r = await authedFetch(`${runtime.apiBase()}/admin/corpora`, {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify(body),
        });
        return jsonOrThrow(r, "adminCorpusCreate");
    },

    /** Admin: soft-delete a corpus by slug. Storage bytes survive —
     * the operator clears those out-of-band. The slug becomes
     * immediately available for reuse. */
    async adminCorpusArchive(slug: string): Promise<void> {
        const r = await authedFetch(
            `${runtime.apiBase()}/admin/corpora/${encodeURIComponent(slug)}`,
            {method: "DELETE"},
        );
        if (!r.ok) {
            throw new ApiError(`adminCorpusArchive(${slug})`, r.status, await readDetail(r));
        }
    },

    /** Admin: kick off a regression sweep across one scope. Enumerates
     * every (source file × viable target) cell from the converter
     * matrix and enqueues a normal convert job per cell with the
     * resulting audit_run id stamped on each row. Cached cells (derived
     * blob already present) count as ``done`` immediately. Returns the
     * fresh run record (status='running', total=0); poll
     * ``adminAuditRunGet`` for progress as the dispatcher fills in
     * ``total`` and counters update as jobs land. */
    async adminAuditRunCreate(
        body: {
            scope: ScopeUrl;
            worker_pool?: string | null;
            note?: string | null;
            force_rebuild?: boolean;
            validate_only?: boolean;
        },
    ): Promise<AuditRun> {
        const r = await authedFetch(`${runtime.apiBase()}/admin/audit/runs`, {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify(body),
        });
        return jsonOrThrow(r, "adminAuditRunCreate");
    },

    /** Cell matrix for an audit run — drives the in-browser (WASM) sweep
     * executor. ``done`` flags cells that already have a terminal audit row
     * for this run, so a reload resumes instead of re-running them. */
    async adminAuditRunCells(runId: string): Promise<{
        run_id: string;
        scope: ScopeUrl;
        cells: Array<{source_key: string; target_format: string; done: boolean}>;
    }> {
        const r = await authedFetch(
            `${runtime.apiBase()}/admin/audit/runs/${encodeURIComponent(runId)}/cells`,
        );
        return jsonOrThrow(r, "adminAuditRunCells");
    },

    /** Admin: ambient summary of currently-running audit sweeps.
     * Drives the bottom-right badge that links into the Audit Runs
     * tab; intentionally cheap so it polls cleanly every 15s.
     * ``current_cell`` surfaces what's actively converting right now
     * (most-recently-touched ``running`` or ``queued`` audit_log row
     * across all live runs). */
    async adminAuditActive(): Promise<{
        running_runs: number;
        pending_cells: number;
        current_cell: {
            key: string | null;
            target_format: string | null;
            status: string | null;
            started_at: string | null;
            elapsed_ms: number | null;
        } | null;
    }> {
        const r = await authedFetch(`${runtime.apiBase()}/admin/audit/active`);
        return jsonOrThrow(r, "adminAuditActive");
    },

    /** Admin: recent audit runs, reverse-chronological. ``before_started_at``
     * is the keyset cursor (ISO timestamp) — pass the previous response's
     * ``next_before_started_at`` to page back further. */
    async adminAuditRunsList(opts?: {
        limit?: number;
        before_started_at?: string | null;
    }): Promise<{runs: AuditRun[]; next_before_started_at: string | null}> {
        const params = new URLSearchParams();
        if (opts?.limit) params.set("limit", String(opts.limit));
        if (opts?.before_started_at) params.set("before_started_at", opts.before_started_at);
        const qs = params.toString();
        const url = `${runtime.apiBase()}/admin/audit/runs${qs ? `?${qs}` : ""}`;
        const r = await authedFetch(url);
        return jsonOrThrow(r, "adminAuditRunsList");
    },

    /** Admin: one run + every audit_log row tied to it. The job list
     * powers the per-cell grid view (files × targets) in the audit
     * panel. Returned in dispatch order (asc by audit_log.id) so
     * grid rendering is deterministic. */
    async adminAuditRunGet(runId: string): Promise<{run: AuditRun; jobs: AuditRunJob[]}> {
        const r = await authedFetch(
            `${runtime.apiBase()}/admin/audit/runs/${encodeURIComponent(runId)}`,
        );
        return jsonOrThrow(r, `adminAuditRunGet(${runId})`);
    },

    /** Admin: abort a running audit. Flips the run to ``aborted``
     * and cancels every queued / running child cell in one
     * transaction. 404 if the run isn't currently ``running``. */
    async adminAuditRunCancel(runId: string): Promise<AuditRun> {
        const r = await authedFetch(
            `${runtime.apiBase()}/admin/audit/runs/${encodeURIComponent(runId)}/cancel`,
            {method: "POST"},
        );
        return jsonOrThrow(r, `adminAuditRunCancel(${runId})`);
    },

    /** Admin: list live audit schedules (M4). Archived rows hidden;
     * the picker only ever wants currently-firing rows. */
    async adminAuditSchedulesList(): Promise<{schedules: AuditSchedule[]}> {
        const r = await authedFetch(`${runtime.apiBase()}/admin/audit/schedules`);
        return jsonOrThrow(r, "adminAuditSchedulesList");
    },

    /** Admin: create a recurring schedule. ``cron_expr`` is validated
     * server-side via croniter — invalid expressions return 400 with
     * the croniter parse error in the body. */
    async adminAuditScheduleCreate(
        body: {
            name: string;
            cron_expr: string;
            scope: string;
            worker_pool?: string | null;
            enabled?: boolean;
        },
    ): Promise<AuditSchedule> {
        const r = await authedFetch(`${runtime.apiBase()}/admin/audit/schedules`, {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify(body),
        });
        return jsonOrThrow(r, "adminAuditScheduleCreate");
    },

    /** Admin: partial update. Only included keys are written; omit
     * a field to leave it alone. Editing ``cron_expr`` recomputes
     * ``next_fire_at`` so the retimed pattern takes effect right
     * away. */
    async adminAuditScheduleUpdate(
        scheduleId: string,
        body: Partial<{
            name: string;
            cron_expr: string;
            scope: string;
            worker_pool: string | null;
            enabled: boolean;
        }>,
    ): Promise<AuditSchedule> {
        const r = await authedFetch(
            `${runtime.apiBase()}/admin/audit/schedules/${encodeURIComponent(scheduleId)}`,
            {
                method: "PATCH",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify(body),
            },
        );
        return jsonOrThrow(r, `adminAuditScheduleUpdate(${scheduleId})`);
    },

    /** Admin: soft-delete a schedule. The tick filter excludes
     * archived rows so the schedule stops firing immediately. */
    async adminAuditScheduleArchive(scheduleId: string): Promise<void> {
        const r = await authedFetch(
            `${runtime.apiBase()}/admin/audit/schedules/${encodeURIComponent(scheduleId)}`,
            {method: "DELETE"},
        );
        if (!r.ok) {
            throw new ApiError(
                `adminAuditScheduleArchive(${scheduleId})`, r.status, await readDetail(r),
            );
        }
    },

    /** Admin: fire a schedule's sweep right now, bypassing the cron
     * slot. Honours the concurrent-fire guard (409 if a previous
     * run with the same (scope, pool) is still in-flight). Does NOT
     * advance ``next_fire_at``. */
    async adminAuditScheduleFireNow(scheduleId: string): Promise<AuditRun> {
        const r = await authedFetch(
            `${runtime.apiBase()}/admin/audit/schedules/${encodeURIComponent(scheduleId)}/fire`,
            {method: "POST"},
        );
        return jsonOrThrow(r, `adminAuditScheduleFireNow(${scheduleId})`);
    },

    /** Admin: read the configured issue-tracker target (M5). Tokens
     * never come back — only the env var name + a present/missing
     * flag for the serving replica. */
    async adminIssueTargetGet(): Promise<IssueTargetConfig> {
        const r = await authedFetch(`${runtime.apiBase()}/admin/audit/issue-target`);
        return jsonOrThrow(r, "adminIssueTargetGet");
    },

    /** Admin: overwrite the issue-tracker target. The actual token
     * is rotated by changing the underlying k8s Secret + re-rolling
     * the API deployment; this endpoint only points at which env
     * var to read. */
    async adminIssueTargetSet(body: {
        kind: "disabled" | "github" | "forgejo";
        repo: string;
        base_url?: string;
        token_env_name?: string;
    }): Promise<IssueTargetConfig> {
        const r = await authedFetch(`${runtime.apiBase()}/admin/audit/issue-target`, {
            method: "PUT",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify(body),
        });
        return jsonOrThrow(r, "adminIssueTargetSet");
    },

    /** Admin: re-run the issue-bot sync for one finished audit run.
     * Clears the prior ``issue_bot_status`` and kicks an immediate
     * sync as a background task so the user gets quick feedback. */
    async adminAuditRunSyncIssues(runId: string): Promise<void> {
        const r = await authedFetch(
            `${runtime.apiBase()}/admin/audit/runs/${encodeURIComponent(runId)}/sync-issues`,
            {method: "POST"},
        );
        if (!r.ok) {
            throw new ApiError(
                `adminAuditRunSyncIssues(${runId})`, r.status, await readDetail(r),
            );
        }
    },

    /** Admin: re-run the issue-bot for ONE failed user conversion
     * (M5b). Mirrors adminAuditRunSyncIssues; the response is 202
     * + the row gets re-claimed by the bot's background task. */
    async adminAuditLogSyncIssue(auditId: number): Promise<void> {
        const r = await authedFetch(
            `${runtime.apiBase()}/admin/audit/${auditId}/sync-issue`,
            {method: "POST"},
        );
        if (!r.ok) {
            throw new ApiError(
                `adminAuditLogSyncIssue(${auditId})`, r.status, await readDetail(r),
            );
        }
    },

    /** Admin: cross-conversion perf snapshot (M6). Aggregates the
     * last ``since`` days of convert jobs into a per (source × target)
     * cell table with p50 / p95 / max metrics + a streaming-candidate
     * verdict on each cell. ``audit_run_id`` + ``worker_image_tag``
     * narrow the snapshot to one sweep / one worker build so old or
     * cached data from a different image doesn't dilute it. */
    async adminPerfReport(opts?: {
        since?: number;
        trigger?: "all" | "audit" | "user";
        audit_run_id?: string;
        worker_image_tag?: string;
    }): Promise<PerfReport> {
        const params = new URLSearchParams();
        if (opts?.since != null) params.set("since", String(opts.since));
        if (opts?.trigger) params.set("trigger", opts.trigger);
        if (opts?.audit_run_id) params.set("audit_run_id", opts.audit_run_id);
        if (opts?.worker_image_tag) params.set("worker_image_tag", opts.worker_image_tag);
        const qs = params.toString();
        const url = `${runtime.apiBase()}/admin/audit/perf${qs ? `?${qs}` : ""}`;
        const r = await authedFetch(url);
        return jsonOrThrow(r, "adminPerfReport");
    },

    /** Admin: distinct worker_image_tag values seen in the perf
     * window, freshest first. Drives the PerformanceTab "Worker SHA"
     * picker — only tags with data behind them appear. */
    async adminPerfWorkers(since = 90): Promise<{
        workers: {tag: string; samples: number; last_seen: string | null}[];
        since_days: number;
    }> {
        const r = await authedFetch(
            `${runtime.apiBase()}/admin/audit/perf/workers?since=${since}`,
        );
        return jsonOrThrow(r, "adminPerfWorkers");
    },

    /** Admin: effective streaming-classifier thresholds, plus the
     * shipped defaults so the UI can label overridden rows. */
    async adminPerfThresholdsGet(): Promise<PerfThresholdsResp> {
        const r = await authedFetch(`${runtime.apiBase()}/admin/audit/perf/thresholds`);
        return jsonOrThrow(r, "adminPerfThresholdsGet");
    },

    /** Admin: write threshold overrides. Pass ``null`` for a key to
     * clear the override (ship-default takes over). Unknown keys
     * 400 — we'd rather catch a typo than silently disable a signal. */
    async adminPerfThresholdsSet(
        body: Record<string, number | null>,
    ): Promise<PerfThresholdsResp> {
        const r = await authedFetch(`${runtime.apiBase()}/admin/audit/perf/thresholds`, {
            method: "PUT",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify(body),
        });
        return jsonOrThrow(r, "adminPerfThresholdsSet");
    },

    /** Admin: function-level hotspots aggregated across recent
     * profiles in one cell. Empty ``functions`` + ``profiles_in_window=0``
     * usually means ``profile_conversions`` was off during the
     * window, or the background parser hasn't caught up yet. */
    async adminPerfHotspots(opts: {
        source_ext?: string;
        target_format?: string;
        since?: number;
        limit?: number;
    }): Promise<PerfHotspotsResp> {
        const params = new URLSearchParams();
        if (opts.source_ext) params.set("source_ext", opts.source_ext);
        if (opts.target_format) params.set("target_format", opts.target_format);
        if (opts.since != null) params.set("since", String(opts.since));
        if (opts.limit != null) params.set("limit", String(opts.limit));
        const qs = params.toString();
        const url = `${runtime.apiBase()}/admin/audit/perf/hotspots${qs ? `?${qs}` : ""}`;
        const r = await authedFetch(url);
        return jsonOrThrow(r, "adminPerfHotspots");
    },

    /** Admin: kick off a background sweep that scans the scope for
     * gzip-compressible source files (.ifc / .step / .sif / etc.)
     * whose stored bytes aren't gzipped, and rewrites each with
     * Content-Encoding: gzip. Returns 202 immediately — poll
     * ``adminCompressionStatus`` for progress. */
    async adminStartCompressionSweep(scope: ScopeUrl): Promise<void> {
        const r = await authedFetch(
            `${runtime.apiBase()}/admin/storage/${encodeURIComponent(scope)}/compress-uncompressed`,
            {method: "POST"},
        );
        if (!r.ok) {
            throw new ApiError(
                `adminStartCompressionSweep(${scope})`, r.status, await readDetail(r),
            );
        }
    },

    /** Admin: snapshot of the in-flight + recently-completed
     * compression sweeps, keyed by scope. */
    async adminCompressionStatus(): Promise<{
        scopes: Record<string, CompressionSweepState>;
    }> {
        const r = await authedFetch(`${runtime.apiBase()}/admin/storage/compression-status`);
        return jsonOrThrow(r, "adminCompressionStatus");
    },

    /** Admin: snapshot of every worker pod that recently checked in.
     * The ``online`` flag is true when ``last_heartbeat`` is within
     * ``stale_after_s`` of ``now`` (both reported by the server so the
     * client doesn't depend on local clock skew). */
    async adminListWorkers(): Promise<{
        workers: WorkerEntry[];
        now: number;
        stale_after_s: number;
    }> {
        const r = await authedFetch(`${runtime.apiBase()}/admin/workers`);
        return jsonOrThrow(r, "adminListWorkers");
    },

    /** Admin: read a key from app_settings. Value is null when unset. */
    async adminGetSetting(key: string): Promise<string | null> {
        const r = await authedFetch(
            `${runtime.apiBase()}/admin/settings/${encodeURIComponent(key)}`,
        );
        const body = await jsonOrThrow<{key: string; value: string | null}>(r, `adminGetSetting(${key})`);
        return body.value;
    },

    /** Admin: set a key in app_settings. Stringified server-side; the
     * caller is responsible for the encoding (e.g. "true"/"false"). */
    async adminSetSetting(key: string, value: string): Promise<void> {
        const r = await authedFetch(
            `${runtime.apiBase()}/admin/settings/${encodeURIComponent(key)}`,
            {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({value}),
            },
        );
        if (!r.ok) {
            throw new ApiError(`adminSetSetting(${key})`, r.status, await readDetail(r));
        }
    },

    /** Direct URL for a profile-dump download. Auth-aware caller
     * should fetch via authedFetch + blob — exposing the URL here
     * keeps it composable with the table's <a download>. */
    adminProfileUrl(auditId: number): string {
        return `${runtime.apiBase()}/admin/audit/${auditId}/profile`;
    },

    /** Mint a 30-day bearer for CLI / pixi-task use. Returned once;
     * the server does not persist it. */
    async adminMintCliToken(): Promise<{token: string; expires_at: number}> {
        const r = await authedFetch(`${runtime.apiBase()}/admin/auth/cli-token`, {
            method: "POST",
        });
        return jsonOrThrow(r, "adminMintCliToken");
    },

    /** Revoke every previously-minted CLI token for the current user
     * by bumping the per-user cutoff. The OIDC bearer used for this
     * request stays valid. */
    async adminRevokeCliTokens(): Promise<{revoked_at: number}> {
        const r = await authedFetch(
            `${runtime.apiBase()}/admin/auth/cli-token/revoke`,
            {method: "POST"},
        );
        return jsonOrThrow(r, "adminRevokeCliTokens");
    },

    /** Trigger the original-source download for an audit row. Used by
     * the local repro pixi tasks but also handy for one-off debugging
     * straight from the admin panel. */
    async adminDownloadAuditSource(auditId: number, suggestedName: string): Promise<void> {
        const r = await authedFetch(
            `${runtime.apiBase()}/admin/audit/${auditId}/source`,
        );
        if (!r.ok) {
            throw new ApiError(`adminDownloadAuditSource(${auditId})`, r.status, await readDetail(r));
        }
        const blob = await r.blob();
        const url = URL.createObjectURL(blob);
        try {
            const a = document.createElement("a");
            a.href = url;
            a.download = suggestedName;
            a.style.display = "none";
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
        } finally {
            URL.revokeObjectURL(url);
        }
    },

    /** Trigger the .prof download with the bearer token attached. */
    async adminDownloadProfile(auditId: number, suggestedName: string): Promise<void> {
        const r = await authedFetch(this.adminProfileUrl(auditId));
        if (!r.ok) {
            throw new ApiError(`adminDownloadProfile(${auditId})`, r.status, await readDetail(r));
        }
        const blob = await r.blob();
        const url = URL.createObjectURL(blob);
        try {
            const a = document.createElement("a");
            a.href = url;
            a.download = suggestedName;
            a.style.display = "none";
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
        } finally {
            URL.revokeObjectURL(url);
        }
    },

    /** Server-parsed profile stats for the dashboard table.
     * Returns one row per function with cumtime / tottime / call counts;
     * the SPA sorts client-side so the user can pivot freely. */
    async adminProfileStats(auditId: number, limit = 500): Promise<ProfileStatsResp> {
        const r = await authedFetch(
            `${runtime.apiBase()}/admin/audit/${auditId}/profile/stats?limit=${limit}`,
        );
        return jsonOrThrow(r, `adminProfileStats(${auditId})`);
    },

    /** Per-heartbeat resource samples (RSS / CPU / IO) captured by the
     * worker subprocess wrapper while the convert child was alive. */
    async adminMetricsHistory(auditId: number): Promise<MetricsHistoryResp> {
        const r = await authedFetch(
            `${runtime.apiBase()}/admin/audit/${auditId}/metrics-history`,
        );
        return jsonOrThrow(r, `adminMetricsHistory(${auditId})`);
    },

    /** Admin: clear all conversion metrics + delete profile blobs.
     * Returns counts so the UI can confirm what was wiped. */
    async adminClearMetrics(): Promise<{rows_cleared: number; profiles_deleted: number; errors: string[]}> {
        const r = await authedFetch(
            `${runtime.apiBase()}/admin/audit/metrics`,
            {method: "DELETE"},
        );
        return jsonOrThrow(r, "adminClearMetrics");
    },

    async adminListProjects(): Promise<AdminProject[]> {
        const r = await authedFetch(`${runtime.apiBase()}/admin/projects`);
        const body = await jsonOrThrow<{projects: AdminProject[]}>(r, "adminListProjects");
        return body.projects;
    },

    async adminCreateProject(slug: string, name: string): Promise<AdminProject> {
        const r = await authedFetch(`${runtime.apiBase()}/admin/projects`, {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({slug, name}),
        });
        return jsonOrThrow<AdminProject>(r, "adminCreateProject");
    },

    /** Provision (or rotate the token of) a synthetic ``ci:<slug>``
     * bot user for a project. Returns the bearer exactly once — the
     * server does not persist it. Re-calling rotates: the per-user
     * revoke cutoff is bumped before the new token is minted, so any
     * tokens issued previously to this bot stop validating. */
    async adminProvisionCiBot(
        projectId: string,
    ): Promise<{user_sub: string; token: string; expires_at: number}> {
        const r = await authedFetch(
            `${runtime.apiBase()}/admin/projects/${encodeURIComponent(projectId)}/ci-bot`,
            {method: "POST"},
        );
        return jsonOrThrow(r, "adminProvisionCiBot");
    },

    async adminArchiveProject(projectId: string): Promise<void> {
        const r = await authedFetch(
            `${runtime.apiBase()}/admin/projects/${encodeURIComponent(projectId)}`,
            {method: "DELETE"},
        );
        if (!r.ok && r.status !== 204) {
            throw new ApiError(`adminArchiveProject failed: ${r.status}`, r.status, await readDetail(r));
        }
    },

    async adminListMembers(projectId: string): Promise<ProjectMember[]> {
        const r = await authedFetch(
            `${runtime.apiBase()}/admin/projects/${encodeURIComponent(projectId)}/members`,
        );
        const body = await jsonOrThrow<{members: ProjectMember[]}>(r, "adminListMembers");
        return body.members;
    },

    async adminAddMember(
        projectId: string,
        userSub: string,
        role: string = "member",
    ): Promise<{user_sub: string; role: string; added: boolean}> {
        const r = await authedFetch(
            `${runtime.apiBase()}/admin/projects/${encodeURIComponent(projectId)}/members`,
            {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({user_sub: userSub, role}),
            },
        );
        return jsonOrThrow(r, "adminAddMember");
    },

    /** Admin: enriched per-scope listing (format, last_modified,
     * derived products). Same scope check as the user-facing /files
     * endpoint — admins still need scope access. */
    async adminListStorage(
        scope: ScopeUrl,
        opts?: {signal?: AbortSignal},
    ): Promise<AdminFileEntry[]> {
        const r = await authedFetch(
            `${runtime.apiBase()}/admin/scopes/${encodeURIComponent(scope)}/files`,
            {signal: opts?.signal},
        );
        const body = await jsonOrThrow<{files: AdminFileEntry[]}>(r, "adminListStorage");
        return body.files;
    },

    /** Admin: delete a source (and all its derived blobs) or a single
     * derived blob. Returns the list of keys actually removed. */
    async adminDeleteBlob(
        scope: ScopeUrl,
        key: string,
    ): Promise<{deleted: string[]; errors?: string[]}> {
        const r = await authedFetch(
            `${runtime.apiBase()}/admin/scopes/${encodeURIComponent(scope)}/blobs/${encodeURIComponent(key)}`,
            {method: "DELETE"},
        );
        return jsonOrThrow(r, "adminDeleteBlob");
    },

    /** Admin: batch-move source keys into a destination folder
     * (key prefix). Each source is renamed to ``<folder>/<basename>``;
     * derived blobs under ``_derived/<src>.*`` follow so the convert
     * cache is preserved. Returns per-key outcomes. */
    async adminMoveKeysToFolder(
        scope: ScopeUrl,
        keys: string[],
        folder: string,
    ): Promise<MoveKeysResult> {
        const r = await authedFetch(
            `${runtime.apiBase()}/admin/scopes/${encodeURIComponent(scope)}/keys/move-to-folder`,
            {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({keys, folder}),
            },
        );
        return jsonOrThrow(r, "adminMoveKeysToFolder");
    },

    /** Admin: rename a single source key in any scope (derived blobs
     * follow). Twin of the user-level renameKey. */
    async adminRenameKey(
        scope: ScopeUrl,
        oldKey: string,
        newKey: string,
    ): Promise<MovedKeyEntry> {
        const r = await authedFetch(
            `${runtime.apiBase()}/admin/scopes/${encodeURIComponent(scope)}/keys/rename`,
            {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({old_key: oldKey, new_key: newKey}),
            },
        );
        return jsonOrThrow(r, "adminRenameKey");
    },

    /** Server-side copy keys from another scope into ``dstScope`` (e.g. pulling
     * files from a project/user scope into a corpus). Garage/S3 CopyObject —
     * no download/reupload. Per-key ``{copied, failed}``. */
    async adminCopyKeysFromScope(
        dstScope: ScopeUrl,
        srcScope: ScopeUrl,
        keys: string[],
    ): Promise<{
        copied: Array<{key: string}>;
        failed: Array<{key: string; reason: string}>;
    }> {
        const r = await authedFetch(
            `${runtime.apiBase()}/admin/scopes/${encodeURIComponent(dstScope)}/keys/copy-from`,
            {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({src_scope: srcScope, keys}),
            },
        );
        return jsonOrThrow(r, "adminCopyKeysFromScope");
    },

    /** Rename or relocate a folder prefix in place. Walks ``allKeys``
     * for entries under ``oldFolder``, groups them by their parent
     * path *relative to* ``oldFolder``, and issues one
     * ``adminMoveKeysToFolder`` call per group with the corresponding
     * ``<newFolder>/<relative_parent>`` destination. Result aggregates
     * per-call ``moved`` + ``failed`` lists.
     *
     * Why grouped calls instead of one big batch: the move endpoint
     * flattens every input key into a single target folder, so a
     * naïve single call would lose the folder's internal structure
     * (``A/sub/x.ifc`` would land at ``B/x.ifc``, not ``B/sub/x.ifc``).
     * Grouping by relative parent preserves the tree shape.
     */
    async adminRenameOrMoveFolder(
        scope: ScopeUrl,
        oldFolder: string,
        newFolder: string,
        allKeys: string[],
    ): Promise<MoveKeysResult> {
        const groups = groupKeysByRelativeParent(oldFolder, newFolder, allKeys);
        const movedAll: MovedKeyEntry[] = [];
        const failedAll: Array<{key: string; reason: string}> = [];
        // Sequential not parallel: each call mutates the scope's keyset
        // on the server; concurrent calls would race on collision
        // detection.
        for (const [dest, keys] of groups) {
            const r = await this.adminMoveKeysToFolder(scope, keys, dest);
            movedAll.push(...r.moved);
            failedAll.push(...r.failed);
        }
        return {moved: movedAll, failed: failedAll};
    },

    async adminRemoveMember(projectId: string, userSub: string): Promise<void> {
        const r = await authedFetch(
            `${runtime.apiBase()}/admin/projects/${encodeURIComponent(projectId)}` +
                `/members/${encodeURIComponent(userSub)}`,
            {method: "DELETE"},
        );
        if (!r.ok && r.status !== 204) {
            throw new ApiError(`adminRemoveMember failed: ${r.status}`, r.status, await readDetail(r));
        }
    },
};

export {ApiError};
