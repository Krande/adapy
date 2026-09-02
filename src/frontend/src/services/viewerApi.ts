// Typed client for the hosted viewer's REST API. Every fetch against
// /api/* should go through this module so the URL shape, error
// handling, auth header, and types live in one place.
//
// Pure module — no React, no zustand. Callers compose with stores.

import { runtime } from "@/runtime/config";
import {
  getAccessToken,
  isAuthEnabled,
  refreshAccessToken,
  signIn,
} from "@/services/auth/oidc";
import { fetchFeaManifest, fetchResultMeta } from "@/services/feaManifestPoll";
import type { FemConcepts } from "@/extensions/design_and_analysis_extension";
import type { ModelStats } from "@/utils/stats/modelStats";

// Known-good target formats keep autocomplete on the call sites that
// hardcode a value (the GLB auto-convert path on upload, etc.), while
// the ``(string & {})`` trailer keeps the type open for whatever new
// targets the worker matrix advertises (.stl, .obj, .step, …) without
// each new pair needing a frontend release.
export type TargetFormat = "glb" | "ifc" | "xml" | (string & {});
export type ConvertStatus =
  | "queued"
  | "running"
  | "done"
  | "error"
  | "cancelled";

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
  failed: Array<{ key: string; reason: string }>;
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
  scopes: Array<{
    kind: "shared" | "user" | "project" | "corpus";
    id: string | null;
    name: string;
  }>;
  projects: Array<{ id: string; slug: string; name: string; role: string }>;
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
  angle_range: { min_deg: number; max_deg: number } | null;
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
  sources: Array<{ scope: string; branch: string; commit: string }>;
  specs: Record<string, ComponentSpecManifestEntry>;
}

export type ComponentsProfilesResponse =
  | { category: string; profiles: string[] }
  | { categories: string[] };

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

export type FeaScalarRange = { [component: string]: [number, number] };

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
  ip_layout: Array<{
    ip: number;
    layer: string;
    in_plane: string;
    /** Optional source-node corner or natural/axial coordinates used for
     * exact result-point marker placement. */
    node_index?: number;
    natural_coordinates?: number[];
  }>;
  /** Element labels in payload order — frontend maps draw-range
   *  labels back to ``element_labels.indexOf(label)`` to find the
   *  row in the AFEL blob. */
  element_labels: number[];
  /** Source mesh node indices for each element row. Enables exact marker
   * placement even when line elements have no triangle draw range. */
  element_node_indices?: number[][];
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
  support:
    | "nodal"
    | "element_nodal"
    | "element_average"
    | "result_point"
    | "line_result_point"
    | "gauss";
  /** Optional source-defined hierarchy. Older manifests omit these and use
   * the existing flat field picker. */
  semantic_key?: string;
  group_path?: string[];
  coordinate_system?: string;
  surface?: string;
  /** Separate AFBL fields that represent surfaces of one semantic nodal
   * result. Element fields normally carry this dimension in ip_layout. */
  surface_variants?: Array<{ surface: string; field_name: string }>;
  derived?: boolean;
  unit?: string;
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
  groups?: {
    name: string;
    members: string[];
    fe_object_type?: "node" | "element";
  }[];
  legacy_glb?: { url_template: string };
  /** Reserved plugin data map (Decision 3). Each key is a plugin id; the value
   *  is OPAQUE to core — a plugin's result-sidecar loader reads its own entry
   *  (`manifest.plugins["<id>"]`) and its `{sidecarPrefix}.*` blobs. Core never
   *  interprets the shape, so the manifest schema stays stable as plugins come
   *  and go. This is the pass-through a result plugin's manifest sub-object
   *  rides on. */
  plugins?: Record<string, unknown>;
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
  type?: "Beam" | "Plate";
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
  constructor(
    message: string,
    public status: number,
    public detail?: string,
  ) {
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
    throw new ApiError(
      `${what} failed: ${r.status} ${r.statusText}`,
      r.status,
      await readDetail(r),
    );
  }
  return (await r.json()) as T;
}

function authHeader(): Record<string, string> {
  const t = getAccessToken();
  return t ? { Authorization: `Bearer ${t}` } : {};
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
async function authedFetch(
  url: string,
  init: RequestInit = {},
): Promise<Response> {
  // Pre-flight: if our cached token has fallen out of the 30s skew
  // window, refresh before sending rather than letting the request
  // 401 first. Background pollers (e.g. the admin audit badge) fire
  // on a timer and would otherwise log a browser-level 401 on every
  // tick that straddles a token expiry.
  if (isAuthEnabled() && !getAccessToken()) {
    await refreshAccessToken();
  }
  const merged: RequestInit = {
    ...init,
    headers: {
      ...(init.headers as Record<string, string> | undefined),
      ...authHeader(),
    },
  };
  let r = await fetch(url, merged);
  if (r.status === 401 && isAuthEnabled()) {
    const refreshed = await refreshAccessToken();
    if (refreshed) {
      r = await fetch(url, {
        ...init,
        headers: {
          ...(init.headers as Record<string, string> | undefined),
          ...authHeader(),
        },
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
  user_email: string | null;
  user_display_name: string | null;
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
  // Stable per-device id (from client_metrics) — distinguishes view/render
  // audit logs by device (e.g. phone vs desktop). Null for server-side rows.
  device_id: string | null;
  // The worker image that processed a convert row; links to its package manifest.
  worker_image_tag: string | null;
  // Conversion engine + effective toggles for a convert row (which tessellator
  // actually ran, incl. an adacpp→occ-builtin fallback, + the options used).
  convert_meta: ConvertMeta | null;
}

export interface ConvertMeta {
  tessellator?: string;
  step_glb_pipeline?: string;
  glb_compression?: string;
  stream_workers?: string | number | null;
  // Wall-clock split of the recorded duration: the conversion proper vs the
  // GLB-compression (meshopt) post-step, so compression cost isn't mistaken
  // for a slower conversion.
  convert_ms?: number | null;
  compress_ms?: number | null;
  // The pod's CPU allotment (cgroup quota) at conversion time, so the metrics chart can render CPU
  // as % utilization across all cores rather than a cumulative ramp.
  cpu_cores?: number | null;
  options?: Record<string, string>;
  // adacpp [STEPPROF-JSON] pipeline summaries, parsed from the captured child log when
  // the profile_conversions toggle was on — one entry per instrumented C++ pipeline run.
  cpp_profile?: CppProfile[];
}

export interface CppProfilePhase {
  name: string;
  ms: number;
  rss_mb: number;
}

export interface CppProfileThread {
  tid: number;
  solids: number;
  busy_ms: number;
}

export interface CppProfile {
  label: string;
  wall_ms: number;
  peak_rss_mb: number;
  cpu_s?: number;
  parallelism?: number;
  vctx?: number;
  nvctx?: number;
  disk_read_mb?: number;
  majflt?: number;
  solids?: number;
  tris?: number;
  max_tris_solid?: number;
  phases: CppProfilePhase[];
  notes?: Record<string, number>;
  threads?: CppProfileThread[];
}

export interface WorkerPackage {
  name: string;
  version: string | null;
  build: string | null;
  channel: string | null;
}

// One audit-sweep record. Returned by /admin/audit/runs endpoints.
// Counters are eventually-consistent — total is set once the
// dispatcher finishes enumerating cells; ok/failed/skipped advance
// as worker outcomes land. status flips to 'finished' when their
// sum equals total.
export interface AuditRun {
  id: string;
  // Short, human-referrable monotonic run number ("Run #42"); the UUID id
  // stays canonical. May be absent on rows from before the seq migration.
  seq?: number | null;
  // Idle time (ms) excluded from the active duration — the gap before a
  // later validation pass folded into the run. UI subtracts it.
  idle_ms?: number | null;
  // Sum of every cell's own duration_ms — the run's active compute time,
  // independent of wall clock. The UI shows this as the run's total runtime.
  cells_duration_ms?: number | null;
  scope: string;
  worker_pool: string | null;
  trigger: string;
  started_at: string;
  finished_at: string | null;
  status: string;
  note: string | null;
  total: number;
  // Auto-validation parity cells counted into `total` upfront but not yet
  // enqueued (the poller dispatches them once the conversion cells land).
  // 0 once the validation pass starts, and for non-auto-validate runs.
  validate_total?: number;
  ok: number;
  failed: number;
  skipped: number;
  created_by: string | null;
  // M7+: when true, the dispatcher bypassed the cached-blob
  // short-circuit. Useful as a UI badge so an unexpected slow
  // run is recognisable as a perf measurement vs a regression.
  force_rebuild: boolean;
  // When true, the finished-run poller auto-fires a follow-up
  // validate_only parity run for the same scope once this run finishes.
  auto_validate?: boolean;
  // Set once a validation pass has been dispatched for this run (via the
  // toggle or the manual button) — used to gate the "Validate" button so a
  // run is validated at most once.
  auto_validate_dispatched_at?: string | null;
  // Set on a derived run (the auto-validation child, or a re-dispatched
  // copy) to the run it was created from.
  parent_run_id?: string | null;
  // M5: issue-bot sync status. NULL until the bot has touched the
  // run; 'syncing' while in flight; terminal 'done'/'skipped'/'failed'.
  issue_bot_status: string | null;
  issue_bot_last_error: string | null;
  issue_bot_synced_at: string | null;
}

// One historic result for a (source key, target_format) cell — newest
// first — backing the grid's right-click "show history" table.
export interface AuditCellHistoryRow {
  id: number;
  ts: string | null;
  status: string;
  error: string | null;
  duration_ms: number | null;
  peak_rss_kb: number | null;
  worker_image_tag: string | null;
  audit_run_id: string | null;
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
  streaming: { is_candidate: boolean; signals: string[] };
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
  // Per-conversion provenance + quality flags (JSONB). Includes
  // ``occ_fallback`` ({count, reasons, geoms}) when the NGEOM/libtess2 path
  // silently fell back to OCC, and ``mesh_flags`` for distorted triangles.
  convert_meta?: {
    occ_fallback?: {
      count: number;
      reasons?: Record<string, number>;
      geoms?: Record<string, number>;
    };
    mesh_flags?: {
      distorted_tris?: number;
      distorted_frac?: number;
      n_tris?: number;
    };
    // Faces with a trim boundary that tessellated to zero triangles (silently dropped geometry).
    geom_health?: { dropped_faces?: number; total_faces?: number };
    [k: string]: unknown;
  } | null;
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
  ts: number; // epoch seconds
  elapsed_s: number; // seconds since job start
  cpu_user_ms: number;
  cpu_sys_ms: number;
  rss_kb: number;
  peak_rss_kb: number;
  read_bytes: number;
  write_bytes: number;
  // Per-thread cumulative CPU (utime+stime, ms) keyed by tid — drives the per-core utilization
  // envelope for the in-process native engine. Absent on older rows / non-native conversions.
  per_thread_cpu_ms?: Record<string, number> | null;
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
  /** Conversion target format (glb / ifc / step / …). */
  target?: string;
  /** Job state (queued / running / done / error). */
  status?: string;
  /** Case-insensitive substring filter on the source filepath/filename. */
  key?: string;
  /** Lower bound on ``ts``: a duration the SERVER resolves ("6h", "30d"), or an
   * ISO-8601 instant for a custom range. Relative forms are deliberately not
   * resolved here — a clock a few minutes fast would silently empty a
   * "last 5 minutes" view. */
  since?: string;
  /** Upper bound on ``ts``, ISO-8601. Only set for a custom range. */
  until?: string;
  before_id?: number;
  limit?: number;
}

/** Aggregate counts behind the Audit tab's Overview. */
/** Queue pressure right now. Ages are of jobs still WAITING — `ts` is the
 * enqueue time, so a queued row's age is its wait so far. Jobs that already ran
 * are absent on purpose: nothing records when a worker picked one up, so a
 * historical wait would have to be invented. */
export interface AuditCongestion {
  queued: number;
  running: number;
  /** Null when nothing is queued — distinct from 0, which would mean
   * "served instantly". */
  oldest_wait_s: number | null;
  mean_wait_s: number | null;
  median_wait_s: number | null;
  /** How many rows carry a recorded start. Rows predating the started_at
   * migration do not, so this travels with the numbers — a median over three
   * rows deserves less trust than one over three thousand. */
  served: number;
  served_mean_wait_s: number | null;
  served_median_wait_s: number | null;
  served_p95_wait_s: number | null;
  served_max_wait_s: number | null;
}

export interface AuditSummary {
  total: number;
  congestion: AuditCongestion;
  /** Always carries the four states the queue writes, zero-filled. */
  by_status: Record<string, number>;
  by_target: { target: string; counts: Record<string, number>; total: number }[];
  top_errors: { error: string; count: number }[];
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
  errors: { key: string; error: string }[];
  error: string | null;
  cancelled: boolean;
  /** Filename currently being compressed, if any. */
  current_key: string | null;
  /** Server marks ``true`` when ``completed_at`` is null and the
   * ``last_update`` heartbeat is older than 90 s — most likely the
   * viewer pod restarted mid-sweep and the BackgroundTask was lost. */
  orphaned: boolean;
}

/** One advertised conversion: a source extension and every target it can produce. */
export interface WorkerConversion {
  from: string;
  to: string[];
}

/** One advertised @utility spec. Only `name`/`description` are rendered; extra keys are tolerated. */
export interface WorkerUtilitySpec {
  name?: string;
  description?: string;

  [key: string]: unknown;
}

/** One worker pod's self-reported registration entry. */
export interface WorkerEntry {
  worker_id: string;
  image_tag: string | null;
  capabilities: string[];
  started_at: number;
  last_heartbeat: number;
  online: boolean;
  // The backend registration payload (worker.py) also advertises these; they are optional
  // because the type historically dropped them and older workers may omit them.
  source_exts?: string[];
  conversions?: WorkerConversion[];
  utilities?: WorkerUtilitySpec[];
}

// ── Procedural cell models (cellbuilder) ─────────────────────────────

export interface ProceduralModelSummary {
  id: string;
  name: string;
  revision: number;
  created_by: string | null;
  created_at: string | null;
  updated_at: string | null;
  latest_glb_key?: string | null;
}

export interface ProceduralModelDetail extends ProceduralModelSummary {
  doc: ProceduralDoc;
}

/** A start-from template for the "New model from template" menu. The list is
 * the union of the demo templates advertised by every currently-live worker
 * (base worker → adapy-default; capability workers → their own), so a template
 * shows exactly while a worker that can build it is up. Instantiation commits
 * `doc` verbatim (for a non-default engine, a thin routing document the engine
 * expands at compile time). */
export interface ProceduralTemplate {
  /** Stable identity (the template slug). */
  id: string;
  name: string;
  /** Engine slug shown in parentheses in the menu, e.g. an external engine. */
  engine: string;
  /** The document committed verbatim when the user picks this template. */
  doc: ProceduralDoc;
}

/** Entity dumps follow ada.topology.entities (TopoSpace / TopoEquipment). */
export interface ProceduralDoc {
  grid?: Record<string, unknown>;
  /** Blueprint compile options (whitelisted server-side), e.g.
   * {reinforce_internal_walls: true}. */
  blueprint?: Record<string, unknown>;
  /** Selected structural blueprint name the compiler dispatches on (the
   * engine-advertised blueprint slug, e.g. "steel_stru"/"none"). Kept OUT of the
   * whitelisted `blueprint` options. Absent = "steel_stru" (backward compatible). */
  blueprint_name?: string;
  /** Named design ruleset slug (routing/penetration rules) resolved by the
   * compiler; unknown/absent falls back to "standard". */
  design_rules?: string;
  /** Selected fabrication-detail engine slug (adds connection joints after the
   * structural build); absent/"none" = structural-only. The cellbuilder seeds
   * `selectedDetailing` from this on open and persists it back on commit. */
  detailing?: string;
  /** When true, catalog equipment with a linked CAD asset render as real CAD
   * geometry (spliced at compile) instead of a box. */
  equipment_cad?: boolean;
  /** Procedural engine slug that authored/compiles this doc; mirrored to the
   * model's engine column on commit so a cloned template routes to the right
   * worker. Absent / "adapy-default" = the built-in engine. */
  engine?: string;
  spaces: Record<string, unknown>[];
  equipments: Record<string, unknown>[];
  openings?: Record<string, unknown>[];
  /** Routed service runs; each entry {NAME, TYPE, MEDIUM?, CONNECTIONS:
   * [{EQUIPMENT, PORT}]}. Rendered by the compiler as pipe/cable runs. */
  systems?: Record<string, unknown>[];
  /** Authored loft (swept) members; each {NAME, STRUCTURE_NAME?, INCLUDE,
   * STATIONS:[{TYPE, X, Y, Z, WIDTH?/HEIGHT?/RADIUS?, SEGMENTS}], PLACEMENT?
   * (4x4 row-major), THICKNESS, SURFACE_ONLY}. A member with N stations
   * compiles to N-1 swept-band plates; the viewer draws band proxies. */
  loft_members?: Record<string, unknown>[];
  /** Cell GROUPS, each carrying its own structural blueprint (a group == one
   * structure). Each space's `STRUCTURE_NAME` names the group it belongs to;
   * ungrouped spaces omit it. Absent/empty = single model-level blueprint
   * (backward compatible). Honoured only by engines advertising
   * `supports_grouping`; the built-in engine ignores it. */
  groups?: { name: string; blueprint: string }[];
}

export interface ProceduralCompileResponse {
  job_id: string | null;
  derived_key: string;
  cached: boolean;
}

/** Result of staging an uploaded workbook for import. `engine` is the slug read
 * from the file's `_ADA_META` sheet, or null for a hand-made / legacy workbook
 * (no metadata) — the frontend then prompts the user to choose an engine. */
export interface ProceduralXlsxDetect {
  source_key: string;
  engine: string | null;
  package?: string | null;
  package_version?: string | null;
  schema_version?: string | null;
}

/** One proposed equipment move that would make a cramped/unroutable run clean.
 * ``from``/``to`` are equipment ORIGINS (X+LX/2, Y+LY/2, Z). */
export interface ProceduralRelocation {
  equipment: string;
  from: [number, number, number];
  to: [number, number, number];
  reason: string;
  fixes: string[];
}

export interface ProceduralRelocationResult {
  proposals: ProceduralRelocation[];
  unresolved: string[];
  baseline_problems: number;
}

/** Where a dropdown type comes from: a built-in ada archetype/kind ("code")
 * or the per-scope postgres catalog ("catalog"). */
export type TypeOrigin = "code" | "catalog";

/** An equipment type offered by the cellbuilder's add-equipment dropdown. */
/** A port summary carried by an equipment dropdown option (drives the viewer's
 * missing-input overlay and the port-glyph overlay). Position/direction_vector
 * are equipment-local (Z-up, same frame as the box origin); `color` is an
 * optional per-port override (see `utils/portColor`). */
export interface TypePortSummary {
  name: string;
  direction: PortDirection;
  category: PortCategory;
  position?: [number, number, number];
  direction_vector?: [number, number, number];
  color?: string | null;
}

export interface ProceduralTypeOption {
  slug: string;
  name: string;
  origin: TypeOrigin;
  id?: string; // present for catalog-origin entries
  ports?: TypePortSummary[];
  /** Whether a CAD asset is linked to this type (catalog origin only) — gates
   * the selected-object "Show as CAD" toggle. */
  has_cad?: boolean;
}

/** A system type offered by the cellbuilder's systems inspector. */
export interface ProceduralSystemTypeOption extends ProceduralTypeOption {
  type: SystemTemplateType; // the base kind (piping/duct/cable/electrical)
  medium?: string | null;
  voltage?: number | null;
}

/** A space-cell type offered by the cellbuilder's ``+ Cell`` picker: a named
 * blueprint carrying the default box extent a freshly-placed cell is seeded with
 * plus optional entity metadata. Built-in ∪ engine-advertised. */
export interface ProceduralCellTypeOption {
  slug: string;
  name: string;
  origin: TypeOrigin;
  size: [number, number, number]; // default (DX, DY, DZ)
  metadata?: Record<string, unknown>;
}

/** An opening type offered by the cellbuilder's ``+ Opening`` picker: a named
 * door/window carrying its reinforcement subtype and the default box extent.
 * Built-in ∪ engine-advertised. */
export interface ProceduralOpeningTypeOption {
  slug: string;
  name: string;
  origin: TypeOrigin;
  subtype: "door" | "window" | "opening";
  size: [number, number, number]; // default (DX, DY, DZ)
}

/** A named design ruleset offered by the cellbuilder's ruleset dropdown. */
export interface ProceduralDesignRulesetOption {
  slug: string;
  name: string;
  description: string;
  origin: TypeOrigin;
}

/** A structural blueprint offered by the cellbuilder's Blueprint dropdown for
 * the selected compile engine. Selecting one sets the document's
 * `blueprint_name`. Built-in ∪ engine-advertised (engine-scoped). */
export interface ProceduralBlueprintOption {
  slug: string;
  name: string;
  description: string;
  /** Advertised parameter fields (same `{name,label,type,default,...}` shape the
   * Detailing tab renders) — the Blueprint panel generates one input per field and
   * writes the value into `doc.blueprint`. Absent/empty for a blueprint with no
   * knobs (e.g. `none`). For `steel_stru` these are the section-profile enums
   * (girder/column/stringer) — switching a girder to a `BG…` box or `TUB…` tube
   * is the "box beams instead of I-beams" knob. */
  fields?: DetailingFieldSpec[];
  origin: TypeOrigin;
}

// ── Equipment-type & system-template catalogs (per-scope) ────────────

export type PortDirection = "IN" | "OUT" | "INOUT";
export type PortCategory = "process" | "electrical" | "signal";

export interface CatalogPort {
  name: string;
  position: [number, number, number];
  direction_vector: [number, number, number];
  direction: PortDirection;
  category: PortCategory;
  /** Optional per-port colour override (`#rrggbb`). When absent the colour is
   * derived from `category` (see `utils/portColor`). */
  color?: string | null;
}

export interface EquipmentTypeDoc {
  bbox: { lx: number; ly: number; lz: number };
  mass: number;
  cog?: [number, number, number] | null;
  ifc_element_class: string;
  // Whether the linked CAD asset is authored in adapy's Z-up convention. True
  // (default) = take it verbatim; false = a glTF-spec Y-up asset re-oriented to
  // Z-up before measuring/splicing. Only meaningful for mesh CAD assets.
  cad_z_up?: boolean;
  ports: CatalogPort[];
}

export interface EquipmentTypeSummary {
  id: string;
  slug: string;
  name: string;
  description: string | null;
  cad_key: string | null;
  revision: number;
  created_by: string | null;
  created_at?: string | null;
  updated_at: string | null;
  preview_glb_key?: string | null;
}

export interface EquipmentTypeDetail extends EquipmentTypeSummary {
  doc: EquipmentTypeDoc;
}

export type SystemTemplateType = "piping" | "duct" | "cable" | "electrical";

export interface SystemTemplateDoc {
  type: SystemTemplateType;
  medium: string | null;
  voltage: number | null;
  pipe_radius: number;
  pipe_wt: number;
}

export interface SystemTemplateSummary {
  id: string;
  slug: string;
  name: string;
  description: string | null;
  revision: number;
  created_by: string | null;
  updated_at: string | null;
}

export interface SystemTemplateDetail extends SystemTemplateSummary {
  doc: SystemTemplateDoc;
}

export type ProceduralEngineKind = "builtin" | "wheel" | "server";

/** Procedural-engine manifest — how a pluggable engine is sourced and run. The
 * deploy key is never here; `deploy_key_secret` is only a Vault secret NAME. */
export interface ProceduralEngineDoc {
  kind: ProceduralEngineKind;
  repo_url: string | null;
  ref: string | null;
  deploy_key_secret: string | null;
  entrypoint: string | null; // "module:callable", signature compile(doc)->bytes
  pyodide_deps: string[];
  wheel_key: string | null; // set by the build worker (Phase 2)
}

export interface ProceduralEngineSummary {
  id: string;
  slug: string;
  name: string;
  description: string | null;
  revision: number;
  /** "builtin" for the always-present adapy engine, "db" for registered ones. */
  origin?: "builtin" | "db";
  /**
   * Whether this engine understands the cellbuilder's cell GROUPS (a group is one
   * structure compiled with its own blueprint). Advertised per engine by the
   * backend (built-ins = false; a capability engine reports true
   * from its live worker). The Groups UI is gated on this flag — never on a
   * hardcoded engine slug.
   */
  supports_grouping?: boolean;
  created_by?: string | null;
  updated_at?: string | null;
}

export interface ProceduralEngineDetail extends ProceduralEngineSummary {
  doc: ProceduralEngineDoc;
}

/** A browser-runnable engine descriptor (from /procedural-engines/{id}/resolve).
 * builtin: dispatch by slug. wheel: micropip-install `wheel_url` (+ pyodide_deps)
 * then dispatch to `entrypoint`. server: not browser-runnable (`ready:false`). */
export interface ProceduralEngineResolved {
  kind: ProceduralEngineKind;
  slug?: string;
  entrypoint: string | null;
  pyodide_deps?: string[];
  wheel_url?: string | null;
  ready: boolean;
}

/** A detailing engine offered by the Compile-settings "Detailing" dropdown: the
 * fabrication-detail stage that adds connection joints AFTER the structural
 * compile. Built-in (`none` + `adapy-default`) ∪ worker-advertised. Selecting one
 * is a COMPILE-time choice (not part of the document); `none` = structural-only. */
/** One generated control in a joint type's option form. `type` picks the input;
 * length fields are advertised in millimetres (`unit: "mm"`). The Detailing tab
 * renders these VERBATIM — nothing joint-specific is hardcoded frontend-side. */
export interface DetailingFieldSpec {
  name: string;
  label?: string;
  type: "number" | "bool" | "enum";
  default: number | boolean | string;
  min?: number;
  max?: number;
  options?: string[];
  unit?: string;
}

/** An advertised joint type: a per-joint toggle (`default_enabled`) plus the
 * generated option `fields`. The Detailing tab is built entirely from this. */
export interface DetailingJointTypeSpec {
  slug: string;
  name: string;
  description?: string;
  default_enabled?: boolean;
  fields?: DetailingFieldSpec[];
}

export interface DetailingEngineSummary {
  slug: string;
  name: string;
  description: string;
  /** true for an in-process builtin (adapy-default) vs an external capability engine. */
  inprocess: boolean;
  worker_capability?: string | null;
  /** Advertised per-joint-type option specs — drives the Detailing tab. */
  joint_types: DetailingJointTypeSpec[];
  origin: TypeOrigin;
}

/** The per-joint-type option map the Detailing tab produces and the compile call
 * ships as `detailing_options`: keyed by joint slug, each `{enabled, <field>: value}`. */
export type DetailingOptionsPayload = Record<
  string,
  Record<string, number | boolean | string>
>;

export const viewerApi = {
  /** Direct URL for the addressable blob endpoint. Includes scope.
   * Only safe to use as `<a href download>` when auth is disabled —
   * with auth on use :func:`downloadBlob` so the bearer token rides
   * along. */
  blobUrl(scope: ScopeUrl, key: string): string {
    return `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/blobs/${encodeURIComponent(key)}`;
  },

  /** Base URL for a plugin's namespaced REST routes: `/api/plugins/{id}` (the
   * frontend twin of the backend's path-prefixed plugin router convention,
   * Decision 3). A plugin builds its own endpoints as `${pluginBase(id)}/...`;
   * core reserves the `/plugins/` path segment and never names a plugin here. */
  pluginBase(id: string): string {
    return `${runtime.apiBase()}/plugins/${encodeURIComponent(id)}`;
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
    const body = await jsonOrThrow<{ files: FileEntry[] }>(
      r,
      `listFiles(${scope})`,
    );
    return body.files;
  },

  /** Saved utility overlays (_overlays/<model>.<utility>.glb) for the scope. The utils
   * menu filters these by the loaded model so an overlay generated on one model only
   * shows when that model is loaded. */
  async listOverlays(
    scope: ScopeUrl,
  ): Promise<{ key: string; size: number; last_modified: string | null }[]> {
    const r = await authedFetch(
      `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/overlays`,
    );
    const body = await jsonOrThrow<{
      overlays: { key: string; size: number; last_modified: string | null }[];
    }>(r, `listOverlays(${scope})`);
    return body.overlays;
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
    const body = await jsonOrThrow<{ files: AdminFileEntry[] }>(
      r,
      `listFilesWithDerived(${scope})`,
    );
    return body.files;
  },

  /** Trigger a browser download of a stored blob. Fetches with auth,
   * materialises a blob: URL, clicks a hidden anchor, then revokes
   * the URL to release memory. Works in both auth-on and auth-off
   * modes — the only cost over `<a href>` is one extra round-trip
   * the browser would have made anyway. */
  async downloadBlob(
    scope: ScopeUrl,
    key: string,
    suggestedName: string,
  ): Promise<void> {
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
  ): Promise<{ deleted: string[]; errors?: string[] }> {
    const r = await authedFetch(this.blobUrl(scope, key), { method: "DELETE" });
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
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ keys, folder }),
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
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ old_key: oldKey, new_key: newKey }),
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
    const failedAll: Array<{ key: string; reason: string }> = [];
    // Sequential not parallel: each call mutates the scope's keyset
    // on the server; concurrent calls would race on collision
    // detection.
    for (const [dest, keys] of groups) {
      const r = await this.moveKeysToFolder(scope, keys, dest);
      movedAll.push(...r.moved);
      failedAll.push(...r.failed);
    }
    return { moved: movedAll, failed: failedAll };
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

  /** Fetch a byte range `[start, end]` (inclusive) of a stored object.
   * Returns the bytes and whether the server honoured the range (206)
   * or ignored it and sent the whole object (200 — e.g. a gzip-at-rest
   * blob that can't be ranged). The FEA viewer uses this to pull a
   * single field step instead of the whole multi-step blob. */
  async getBlobRange(
    scope: ScopeUrl,
    key: string,
    start: number,
    end: number,
  ): Promise<{ buf: ArrayBuffer; ranged: boolean }> {
    // Send the range BOTH as ?range_start/range_end query params and as a
    // Range header. The query params are proxy-proof — some ingresses/CDNs
    // (seen on the mobile path) strip the Range header, which would
    // silently return the whole multi-step blob. The header stays for any
    // cache/proxy that prefers it; the server replies 206 either way.
    const base = this.blobUrl(scope, key);
    const url = `${base}${base.includes("?") ? "&" : "?"}range_start=${start}&range_end=${end}`;
    const r = await authedFetch(url, {
      headers: { Range: `bytes=${start}-${end}` },
    });
    if (!r.ok && r.status !== 206) {
      throw new ApiError(`getBlobRange(${key})`, r.status, await readDetail(r));
    }
    const buf = await r.arrayBuffer();
    // 206 ⇒ honoured (header or query-param path); 200 ⇒ a proxy/old
    // backend served the whole object → caller parses + slices.
    return { buf, ranged: r.status === 206 };
  },

  /** Upload bytes under a given key. body is anything fetch/XHR can
   * send (File, Blob, ArrayBuffer, ...). When `onProgress` is given,
   * the request goes through XMLHttpRequest because fetch doesn't
   * expose upload progress consistently across browsers. */
  async putBlob(
    scope: ScopeUrl,
    key: string,
    body: BodyInit,
    opts?: { onProgress?: (loaded: number, total: number) => void },
  ): Promise<void> {
    if (!opts?.onProgress) {
      const r = await authedFetch(this.blobUrl(scope, key), {
        method: "PUT",
        body,
        headers: { "Content-Type": "application/octet-stream" },
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
      headers: { "Content-Type": "application/octet-stream" },
    });
    if (!r.ok) {
      throw new ApiError(
        `putDerivedBlob(${sourceKey})`,
        r.status,
        await readDetail(r),
      );
    }
    const j: { key: string; size: number } = await r.json();
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
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      },
    );
    const j = await jsonOrThrow<{ job_id: string }>(r, "auditLocalCreate");
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
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      },
    );
    if (!r.ok) {
      throw new ApiError(
        `auditLocalUpdate(${jobId})`,
        r.status,
        await readDetail(r),
      );
    }
  },

  /** Upload a browser-baked FEA artefact tree (a zip of fea.manifest.json
   * + fea.mesh.glb + fea.*.bin) produced by the in-browser FEM stack. The
   * server unpacks it under ``_derived/<source>.fea/`` with the worker's
   * gzip policy. Returns the manifest key. */
  async uploadFeaArtefacts(
    scope: ScopeUrl,
    sourceKey: string,
    zip: BodyInit,
  ): Promise<string> {
    const url =
      `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/fea/artefacts` +
      `?source=${encodeURIComponent(sourceKey)}`;
    const r = await authedFetch(url, {
      method: "POST",
      body: zip,
      headers: { "Content-Type": "application/zip" },
    });
    const j = await jsonOrThrow<{ manifest_key: string; count: number }>(
      r,
      "uploadFeaArtefacts",
    );
    return j.manifest_key;
  },

  /** Build the per-file FEA artefact upload target (URL + auth headers) for
   * the pyodide worker's synchronous-XHR POSTs (``POST /fea/artefact``, one
   * file each). The worker thread can't reach the SPA's auth module, so we
   * capture the bearer header now; all of one bake's POSTs ride this token.
   * The ``manifest_key`` for the streamed tree is deterministic, so the
   * caller computes it without a round-trip. */
  feaArtefactUploadTarget(
    scope: ScopeUrl,
    sourceKey: string,
  ): { url: string; headers: Record<string, string> } {
    const url =
      `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/fea/artefact` +
      `?source=${encodeURIComponent(sourceKey)}`;
    return { url, headers: authHeader() };
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
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ key }),
      },
    );
    return jsonOrThrow(r, `requestUploadUrl(${key})`);
  },

  /** Request a presigned GET URL for direct, Range-capable download from
   * the object store. Mirrors requestUploadUrl. Used by the in-browser
   * streaming converter to read a huge source (e.g. a multi-GB SIN) in
   * ranges without API-tunneling the whole transfer. Local-backed
   * deployments 503 here — callers fall back to the buffered getBlob path. */
  async requestDownloadUrl(
    scope: ScopeUrl,
    key: string,
  ): Promise<{
    url: string;
    key: string;
    method: string;
    expires_in_seconds: number;
    size: number;
  }> {
    const r = await authedFetch(
      `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/download-url`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ key }),
      },
    );
    return jsonOrThrow(r, `requestDownloadUrl(${key})`);
  },

  /** Finalise a presigned-URL upload: server confirms the object
   * landed and writes the audit row. Caller should run this only
   * after a successful direct PUT — otherwise it 404s. */
  async completeUpload(
    scope: ScopeUrl,
    key: string,
  ): Promise<{ key: string; size: number }> {
    const r = await authedFetch(
      `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/upload-complete`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ key }),
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
      // hardcoded set (use_sat_pcurves / skip_shapefix /
      // profile_conversions) that still ride
      // the env-var rail. Values are tri-state native:
      // ``null`` clears any global override; otherwise the
      // type matches the option's declared ``type``.
      conversionOptions?: Record<string, boolean | string | number | null>;
      // Re-convert: always re-run and write to the separate ``_reconvert/`` namespace so
      // a corpus scope's ``_derived/`` audit product is never overwritten.
      reconvert?: boolean;
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
    if (opts?.reconvert) {
      body.reconvert = true;
    }
    const r = await authedFetch(
      `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/convert`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      },
    );
    return jsonOrThrow<ConvertResponse>(
      r,
      `convert(${sourceKey} -> ${targetFormat})`,
    );
  },

  /** Poll a single conversion job by id. Job_id is globally unique,
   * so the URL doesn't carry a scope — the server re-checks access
   * against the scope recorded on the job. */
  async convertStatus(jobId: string): Promise<ConvertResponse> {
    const r = await authedFetch(
      `${runtime.apiBase()}/convert/${encodeURIComponent(jobId)}`,
    );
    return jsonOrThrow<ConvertResponse>(r, `convertStatus(${jobId})`);
  },

  /** Enqueue an on-demand backend job for a plugin. Generic — core names no
   *  plugin here; `options` is passed opaquely to the plugin's job_entrypoint.
   *
   *  Returns `{job_id, derived_key}` only (NOT a status): poll via
   *  `convertStatus(job_id)` and, on `done`, read the JSON summary with
   *  `getBlob(scope, derived_key)`. Core hashes `options` into the job's
   *  synthetic source key, so an identical repeat request cache-hits a finished
   *  job — add a `refresh` token to the options to deliberately miss that. */
  async pluginJob(
    pluginId: string,
    body: {
      options: Record<string, unknown>;
      derived_key?: string;
      derived_prefix?: string;
      capability?: string;
    },
    opts?: { scope?: ScopeUrl },
  ): Promise<{ job_id: string; derived_key: string }> {
    const base = `${runtime.apiBase()}/plugins/${encodeURIComponent(pluginId)}/jobs`;
    const url = opts?.scope
      ? `${base}?scope=${encodeURIComponent(opts.scope)}`
      : base;
    const r = await authedFetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    return jsonOrThrow<{ job_id: string; derived_key: string }>(
      r,
      `pluginJob(${pluginId})`,
    );
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
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          source_key: sourceKey,
          utility_name: utilityName,
          kwargs,
        }),
      },
    );
    return jsonOrThrow<ConvertResponse>(
      r,
      `runUtility(${utilityName} on ${sourceKey})`,
    );
  },

  // ── Procedural cell models (cellbuilder) ─────────────────────────
  //
  // Backed by /api/scopes/{scope}/procedural-models*; compile jobs
  // flow through the same NATS queue, so status polling reuses
  // convertStatus.

  async listProceduralModels(
    scope: ScopeUrl,
  ): Promise<ProceduralModelSummary[]> {
    const r = await authedFetch(
      `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/procedural-models`,
    );
    const body = await jsonOrThrow<{ models: ProceduralModelSummary[] }>(
      r,
      `listProceduralModels(${scope})`,
    );
    return body.models;
  },

  /** Server-advertised start-from templates: the scope's seeded example models,
   * with worker-backed engines gated on a live worker. The adapy-default
   * built-ins are added client-side; this list is appended to them. */
  async listProceduralTemplates(
    scope: ScopeUrl,
  ): Promise<ProceduralTemplate[]> {
    const r = await authedFetch(
      `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/procedural-templates`,
    );
    const body = await jsonOrThrow<{ templates: ProceduralTemplate[] }>(
      r,
      `listProceduralTemplates(${scope})`,
    );
    return body.templates;
  },

  async createProceduralModel(
    scope: ScopeUrl,
    name: string,
  ): Promise<ProceduralModelDetail> {
    const r = await authedFetch(
      `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/procedural-models`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name }),
      },
    );
    return jsonOrThrow<ProceduralModelDetail>(
      r,
      `createProceduralModel(${name})`,
    );
  },

  /** Rename a procedural model — which is also how it MOVES between folders.
   *
   * The name carries the folder path; a model is addressed by UUID everywhere,
   * so a "/" in it is a label, not a route. One operation rather than two that
   * could disagree about where a model lives. */
  async renameProceduralModel(
    scope: ScopeUrl,
    modelId: string,
    name: string,
  ): Promise<ProceduralModelSummary> {
    const r = await authedFetch(
      `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/procedural-models/${encodeURIComponent(modelId)}/name`,
      {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name }),
      },
    );
    return jsonOrThrow<ProceduralModelSummary>(r, `renameProceduralModel(${name})`);
  },

  async getProceduralModel(
    scope: ScopeUrl,
    modelId: string,
  ): Promise<ProceduralModelDetail> {
    const r = await authedFetch(
      `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/procedural-models/${encodeURIComponent(modelId)}`,
    );
    return jsonOrThrow<ProceduralModelDetail>(
      r,
      `getProceduralModel(${modelId})`,
    );
  },

  /** Commit the doc under optimistic concurrency. 409 (ApiError.status)
   * means someone else committed first — refetch and re-apply. */
  async commitProceduralModel(
    scope: ScopeUrl,
    modelId: string,
    doc: ProceduralDoc,
    baseRevision: number,
  ): Promise<{ id: string; revision: number }> {
    const r = await authedFetch(
      `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/procedural-models/${encodeURIComponent(modelId)}`,
      {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ doc, base_revision: baseRevision }),
      },
    );
    return jsonOrThrow<{ id: string; revision: number }>(
      r,
      `commitProceduralModel(${modelId})`,
    );
  },

  async deleteProceduralModel(scope: ScopeUrl, modelId: string): Promise<void> {
    const r = await authedFetch(
      `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/procedural-models/${encodeURIComponent(modelId)}`,
      { method: "DELETE" },
    );
    if (!r.ok) {
      throw new ApiError(
        `deleteProceduralModel(${modelId})`,
        r.status,
        await readDetail(r),
      );
    }
  },

  async compileProceduralModel(
    scope: ScopeUrl,
    modelId: string,
    force = false,
    lod: "sim" | "detail" = "sim",
    engine?: string | null,
    detailing?: string | null,
    detailingOptions?: DetailingOptionsPayload | null,
  ): Promise<ProceduralCompileResponse> {
    // force=true recompiles even if the revision's GLB is cached — used when the
    // compiler engine changed but the document (the cache key) didn't.
    // lod=detail compiles the richer detail model into a separate cache key.
    // engine selects the procedural engine. Pass it whenever the caller made an
    // explicit choice — INCLUDING "adapy-default": otherwise the server falls
    // back to the model's stored engine, so picking adapy-default on a capability-engine
    // model would silently still compile with that engine. Omit only when the
    // caller passes null/undefined (e.g. auto-compile on instantiate, which
    // should honour the model's stored engine). adapy-default shares the bare
    // cache key with the no-engine case, so this is cache-safe.
    const params = new URLSearchParams();
    if (force) params.set("force", "true");
    if (lod === "detail") params.set("lod", "detail");
    if (engine) params.set("engine", engine);
    // Detailing is a compile-time choice; "none" (the default) adds no key
    // suffix server-side, so omit it to keep the bare (backward-compat) key.
    if (detailing && detailing !== "none") params.set("detailing", detailing);
    // Per-joint detailing options ride as a JSON query param; folded into the
    // server's cache key (a knob change is a distinct entry) and passed to
    // detail(). Only meaningful when a detailing engine is selected.
    if (detailing && detailing !== "none" && detailingOptions)
      params.set("detailing_options", JSON.stringify(detailingOptions));
    const qs = params.toString() ? `?${params.toString()}` : "";
    const r = await authedFetch(
      `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/procedural-models/${encodeURIComponent(modelId)}/compile${qs}`,
      { method: "POST" },
    );
    return jsonOrThrow<ProceduralCompileResponse>(
      r,
      `compileProceduralModel(${modelId})`,
    );
  },

  /** Build the CURRENT (uncommitted) document as an ephemeral preview — no
   * commit, no revision bump. The server keys the GLB on the doc's content hash
   * (re-previewing an unchanged doc is free) and, on a later commit of the same
   * doc, promotes this blob to the revision. `force` re-builds past the cache;
   * `engine`/`lod` mirror compileProceduralModel. */
  async previewProceduralModel(
    scope: ScopeUrl,
    modelId: string,
    doc: unknown,
    opts?: {
      engine?: string | null;
      lod?: "sim" | "detail";
      force?: boolean;
      detailing?: string | null;
      detailingOptions?: DetailingOptionsPayload | null;
    },
  ): Promise<ProceduralCompileResponse> {
    const params = new URLSearchParams();
    if (opts?.force) params.set("force", "true");
    if (opts?.lod === "detail") params.set("lod", "detail");
    if (opts?.engine) params.set("engine", opts.engine);
    if (opts?.detailing && opts.detailing !== "none")
      params.set("detailing", opts.detailing);
    if (opts?.detailing && opts.detailing !== "none" && opts.detailingOptions)
      params.set("detailing_options", JSON.stringify(opts.detailingOptions));
    const qs = params.toString() ? `?${params.toString()}` : "";
    const r = await authedFetch(
      `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/procedural-models/${encodeURIComponent(modelId)}/compile-preview${qs}`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ doc }),
      },
    );
    return jsonOrThrow<ProceduralCompileResponse>(
      r,
      `previewProceduralModel(${modelId})`,
    );
  },

  /** Fetch the log of ONE compile run. Pass `runId` — the `job_id` the
   * compile/preview response returned — and you get exactly that run's log, so a
   * recompile of an unchanged document can never be handed the previous run's
   * output. `derivedKey` is the fallback for a result served from cache (no run
   * happened just now): the server resolves the artifact's `.run` pointer to
   * whichever run last targeted it. Returns `{text, runId}` — `runId` is the run
   * the server actually served ("" for a pre-runs artifact), so the caller can
   * tell a fresh log from an inherited one. Never throws on a missing log. */
  async proceduralCompileLog(
    scope: ScopeUrl,
    modelId: string,
    derivedKey: string,
    runId?: string | null,
  ): Promise<{ text: string; runId: string }> {
    const qs = runId
      ? `?run=${encodeURIComponent(runId)}`
      : `?key=${encodeURIComponent(derivedKey)}`;
    const r = await authedFetch(
      `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/procedural-models/${encodeURIComponent(modelId)}/compile-log${qs}`,
    );
    if (!r.ok) return { text: "", runId: "" };
    return {
      text: await r.text(),
      runId: r.headers.get("X-Compile-Run") || runId || "",
    };
  },

  /** Fetch the quantity take-off computed alongside a compiled GLB (the data
   * behind the viewer Stats panel). `derivedKey` is the compile response's GLB
   * key; the stats are its `.stats.json` sibling. Returns `{available:false}`
   * (HTTP 200) for models without a take-off (a capability engine / STEP-IFC imports) so
   * the panel degrades gracefully rather than erroring. */
  async fetchModelStats(
    scope: ScopeUrl,
    modelId: string,
    derivedKey: string,
  ): Promise<{ available: boolean; stats?: ModelStats }> {
    const qs = `?key=${encodeURIComponent(derivedKey)}`;
    const r = await authedFetch(
      `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/procedural-models/${encodeURIComponent(modelId)}/stats${qs}`,
    );
    if (!r.ok) return { available: false };
    return (await r.json()) as { available: boolean; stats?: ModelStats };
  },

  /** Download the take-off export — a whole-model `.xlsx` workbook (`fmt:"xlsx"`,
   * one sheet per discipline + COGs + Overview) or the active tab as `.csv`
   * (`fmt:"csv"`, `tab` = the open discipline tab). Built on the fly from the
   * stored stats sidecar; fetched WITH auth (bearer rides on the request) and
   * saved via an object URL so it works in both auth-on and auth-off modes. */
  async downloadStatsExport(
    scope: ScopeUrl,
    modelId: string,
    derivedKey: string,
    fmt: "xlsx" | "csv",
    tab?: string,
  ): Promise<void> {
    const params = new URLSearchParams({ key: derivedKey, fmt });
    if (tab) params.set("tab", tab);
    const r = await authedFetch(
      `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/procedural-models/${encodeURIComponent(modelId)}/stats/export?${params.toString()}`,
    );
    if (!r.ok) {
      throw new ApiError(`downloadStatsExport(${fmt})`, r.status, await readDetail(r));
    }
    const blob = await r.blob();
    const cd = r.headers.get("Content-Disposition") || "";
    const m = /filename="?([^"]+)"?/.exec(cd);
    const suggestedName = m ? m[1] : `stats.${fmt}`;
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

  /** Enqueue an export of the model's committed revision to its engine's Excel
   * workbook. Poll `convertStatus(job_id)`; on `done` the `.xlsx` lives at
   * `derived_key` — download it as an attachment (blob GET). `cached:true` +
   * `job_id:null` means the workbook was already built. */
  async exportProceduralModelXlsx(
    scope: ScopeUrl,
    modelId: string,
    opts?: { engine?: string | null; force?: boolean },
  ): Promise<ProceduralCompileResponse> {
    const params = new URLSearchParams();
    if (opts?.force) params.set("force", "true");
    if (opts?.engine) params.set("engine", opts.engine);
    const qs = params.toString() ? `?${params.toString()}` : "";
    const r = await authedFetch(
      `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/procedural-models/${encodeURIComponent(modelId)}/export-xlsx${qs}`,
      { method: "POST" },
    );
    return jsonOrThrow<ProceduralCompileResponse>(
      r,
      `exportProceduralModelXlsx(${modelId})`,
    );
  },

  /** Enqueue an export of the committed revision to a downloadable CAD/analysis
   * file: `format: "ifc"` (the DETAIL model — clash cuts as IfcRelVoidsElement
   * voids, equipment as IfcPump/IfcTank/…) or `"gxml"` (the SIMULATION model as a
   * Genie concept XML). Built-in engine only. Poll `convertStatus` then
   * `downloadBlob`, exactly like `exportProceduralModelXlsx`. */
  async exportProceduralModel(
    scope: ScopeUrl,
    modelId: string,
    format: "ifc" | "gxml",
    opts?: { force?: boolean; cad?: boolean },
  ): Promise<ProceduralCompileResponse> {
    const params = new URLSearchParams({ format });
    if (opts?.force) params.set("force", "true");
    // IFC only: splice real catalog CAD geometry for equipment (default on server-
    // side). Only send when explicitly off, to keep the default URL clean.
    if (format === "ifc" && opts?.cad === false) params.set("cad", "false");
    const r = await authedFetch(
      `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/procedural-models/${encodeURIComponent(modelId)}/export-model?${params.toString()}`,
      { method: "POST" },
    );
    return jsonOrThrow<ProceduralCompileResponse>(
      r,
      `exportProceduralModel(${modelId}, ${format})`,
    );
  },

  /** Stage an uploaded `.xlsx` for import and auto-detect its owning engine from
   * the file's `_ADA_META` sheet (read server-side, dependency-free). Returns the
   * staged `source_key` + detected `engine` (null when the workbook has no
   * metadata — the caller then prompts). Pass the result to
   * `importProceduralModelXlsx`. */
  async uploadProceduralImportXlsx(
    scope: ScopeUrl,
    data: Blob | ArrayBuffer,
  ): Promise<ProceduralXlsxDetect> {
    const r = await authedFetch(
      `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/procedural-models/import-xlsx/upload`,
      {
        method: "POST",
        headers: { "Content-Type": "application/octet-stream" },
        body: data,
      },
    );
    return jsonOrThrow<ProceduralXlsxDetect>(r, `uploadProceduralImportXlsx`);
  },

  /** Enqueue an import of a staged workbook into a NEW model, built by `engine`'s
   * capability pool. Poll `convertStatus(job_id)`; on `done` GET the JSON result
   * blob at `derived_key` (`{model_id, name, engine, revision}`) to open it. */
  async importProceduralModelXlsx(
    scope: ScopeUrl,
    body: { source_key: string; engine: string; name: string },
  ): Promise<{ job_id: string; derived_key: string }> {
    const r = await authedFetch(
      `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/procedural-models/import-xlsx`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      },
    );
    return jsonOrThrow<{ job_id: string; derived_key: string }>(
      r,
      `importProceduralModelXlsx(${body.name})`,
    );
  },

  /** Equipment types for the cellbuilder's add-equipment dropdown: the union
   * of code-defined archetypes (worker pool) and the per-scope DB catalog,
   * each tagged with its origin. */
  async proceduralEquipmentTypes(
    scope: ScopeUrl,
  ): Promise<ProceduralTypeOption[]> {
    const r = await authedFetch(
      `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/procedural-models/equipment-types`,
    );
    const body = await jsonOrThrow<{ equipment_types: ProceduralTypeOption[] }>(
      r,
      `proceduralEquipmentTypes(${scope})`,
    );
    return body.equipment_types;
  },

  /** System types for the cellbuilder's systems inspector: the union of
   * code-defined system kinds (worker pool) and the per-scope DB
   * system-template catalog, each tagged with its origin and base kind. */
  async proceduralSystemTypes(
    scope: ScopeUrl,
  ): Promise<ProceduralSystemTypeOption[]> {
    const r = await authedFetch(
      `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/procedural-models/system-types`,
    );
    const body = await jsonOrThrow<{
      system_types: ProceduralSystemTypeOption[];
    }>(r, `proceduralSystemTypes(${scope})`);
    return body.system_types;
  },

  /** Named design rulesets (routing/penetration rules) for the cellbuilder's
   * ruleset dropdown: the built-in rulesets plus any advertised by live
   * workers. Selecting one sets doc.design_rules. */
  async proceduralDesignRulesets(
    scope: ScopeUrl,
  ): Promise<ProceduralDesignRulesetOption[]> {
    const r = await authedFetch(
      `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/procedural-models/design-rulesets`,
    );
    const body = await jsonOrThrow<{
      design_rulesets: ProceduralDesignRulesetOption[];
    }>(r, `proceduralDesignRulesets(${scope})`);
    return body.design_rulesets;
  },

  /** Structural blueprints for the cellbuilder's Blueprint dropdown, scoped to
   * the compile `engine`: the engine's built-ins (adapy-default: steel_stru /
   * none) plus any advertised by live workers for that engine. Selecting one
   * sets doc.blueprint_name; the first entry is the engine's default. */
  async proceduralBlueprints(
    scope: ScopeUrl,
    engine: string,
  ): Promise<ProceduralBlueprintOption[]> {
    const r = await authedFetch(
      `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/procedural-models/blueprints?engine=${encodeURIComponent(engine)}`,
    );
    const body = await jsonOrThrow<{
      blueprints: ProceduralBlueprintOption[];
    }>(r, `proceduralBlueprints(${scope}, ${engine})`);
    return body.blueprints;
  },

  /** Space-cell types for the cellbuilder's + Cell picker: the union of the
   * built-in blueprints and any advertised by live workers, each tagged with its
   * origin and default size. */
  async proceduralCellTypes(
    scope: ScopeUrl,
  ): Promise<ProceduralCellTypeOption[]> {
    const r = await authedFetch(
      `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/procedural-models/cell-types`,
    );
    const body = await jsonOrThrow<{ cell_types: ProceduralCellTypeOption[] }>(
      r,
      `proceduralCellTypes(${scope})`,
    );
    return body.cell_types;
  },

  /** Opening types for the cellbuilder's + Opening picker: the union of the
   * built-in door/window types and any advertised by live workers, each tagged
   * with its origin, subtype and default size. */
  async proceduralOpeningTypes(
    scope: ScopeUrl,
  ): Promise<ProceduralOpeningTypeOption[]> {
    const r = await authedFetch(
      `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/procedural-models/opening-types`,
    );
    const body = await jsonOrThrow<{
      opening_types: ProceduralOpeningTypeOption[];
    }>(r, `proceduralOpeningTypes(${scope})`);
    return body.opening_types;
  },

  /** Persist a code-defined equipment archetype into this scope's DB catalog
   * so it becomes an editable entry. */
  async syncProceduralEquipmentType(
    scope: ScopeUrl,
    slug: string,
  ): Promise<{ id: string; slug: string; revision: number }> {
    const r = await authedFetch(
      `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/procedural-models/equipment-types/sync`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ slug }),
      },
    );
    return jsonOrThrow(r, `syncProceduralEquipmentType(${scope}, ${slug})`);
  },

  /** Resync ALL code-defined equipment archetypes into this scope's catalog,
   * updating existing entries (unlike the single-slug sync, which only creates).
   * Returns which slugs were created / updated / left unchanged. */
  async resyncProceduralEquipmentTypes(scope: ScopeUrl): Promise<{
    created: string[];
    updated: string[];
    unchanged: string[];
    skipped: string[];
    /** Per-slug human-readable "what changed" (created/updated slugs only). */
    changes: Record<string, string[]>;
  }> {
    const r = await authedFetch(
      `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/procedural-models/equipment-types/resync`,
      { method: "POST" },
    );
    return jsonOrThrow(r, `resyncProceduralEquipmentTypes(${scope})`);
  },

  /** Enqueue a relocation analysis: propose the minimum equipment moves that
   * would make the model's cramped/unroutable runs clean. Returns a job to poll
   * (convertStatus); on done, GET the derived_key blob via
   * fetchProceduralRelocations. Never applied automatically. */
  async proposeProceduralRelocations(
    scope: ScopeUrl,
    modelId: string,
  ): Promise<{ job_id: string | null; derived_key: string }> {
    const r = await authedFetch(
      `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/procedural-models/${encodeURIComponent(modelId)}/propose-relocations`,
      { method: "POST" },
    );
    return jsonOrThrow(r, `proposeProceduralRelocations(${modelId})`);
  },

  /** Fetch the relocation proposals JSON produced by the worker. */
  async fetchProceduralRelocations(
    scope: ScopeUrl,
    key: string,
  ): Promise<ProceduralRelocationResult> {
    const r = await authedFetch(this.blobUrl(scope, key));
    if (!r.ok) {
      throw new ApiError(
        `fetchProceduralRelocations(${key})`,
        r.status,
        await readDetail(r),
      );
    }
    return (await r.json()) as ProceduralRelocationResult;
  },

  /** Fetch an import job's JSON result blob (`{model_id, name, engine,
   * revision}`) written by the worker at `derived_key`. */
  async fetchProceduralImportResult(
    scope: ScopeUrl,
    key: string,
  ): Promise<{ model_id: string; name: string; engine: string | null; revision: number }> {
    const r = await authedFetch(this.blobUrl(scope, key));
    if (!r.ok) {
      throw new ApiError(
        `fetchProceduralImportResult(${key})`,
        r.status,
        await readDetail(r),
      );
    }
    return (await r.json()) as {
      model_id: string;
      name: string;
      engine: string | null;
      revision: number;
    };
  },

  /** Persist a code-defined system kind into this scope's DB system-template
   * catalog. */
  async syncProceduralSystemType(
    scope: ScopeUrl,
    slug: string,
  ): Promise<{ id: string; slug: string; revision: number }> {
    const r = await authedFetch(
      `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/procedural-models/system-types/sync`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ slug }),
      },
    );
    return jsonOrThrow(r, `syncProceduralSystemType(${scope}, ${slug})`);
  },

  // ── Equipment-type catalog (admin panel) ─────────────────────────

  async listEquipmentTypes(scope: ScopeUrl): Promise<EquipmentTypeSummary[]> {
    const r = await authedFetch(
      `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/equipment-types`,
    );
    const body = await jsonOrThrow<{ equipment_types: EquipmentTypeSummary[] }>(
      r,
      `listEquipmentTypes(${scope})`,
    );
    return body.equipment_types;
  },

  async createEquipmentType(
    scope: ScopeUrl,
    name: string,
    slug?: string,
    description?: string,
  ): Promise<EquipmentTypeDetail> {
    const r = await authedFetch(
      `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/equipment-types`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, slug, description }),
      },
    );
    return jsonOrThrow<EquipmentTypeDetail>(r, `createEquipmentType(${name})`);
  },

  async getEquipmentType(
    scope: ScopeUrl,
    typeId: string,
  ): Promise<EquipmentTypeDetail> {
    const r = await authedFetch(
      `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/equipment-types/${encodeURIComponent(typeId)}`,
    );
    return jsonOrThrow<EquipmentTypeDetail>(r, `getEquipmentType(${typeId})`);
  },

  /** Commit metadata + doc under optimistic concurrency. 409 = someone else
   * committed first (refetch) or a slug collision. */
  async updateEquipmentType(
    scope: ScopeUrl,
    typeId: string,
    fields: {
      name: string;
      slug?: string;
      description?: string | null;
      doc: EquipmentTypeDoc;
    },
    baseRevision: number,
  ): Promise<{ id: string; revision: number }> {
    const r = await authedFetch(
      `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/equipment-types/${encodeURIComponent(typeId)}`,
      {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...fields, base_revision: baseRevision }),
      },
    );
    return jsonOrThrow<{ id: string; revision: number }>(
      r,
      `updateEquipmentType(${typeId})`,
    );
  },

  async deleteEquipmentType(scope: ScopeUrl, typeId: string): Promise<void> {
    const r = await authedFetch(
      `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/equipment-types/${encodeURIComponent(typeId)}`,
      { method: "DELETE" },
    );
    if (!r.ok)
      throw new ApiError(
        `deleteEquipmentType(${typeId})`,
        r.status,
        await readDetail(r),
      );
  },

  /** Attach a CAD/GLB asset by direct body upload (filename supplies the
   * extension). */
  async uploadEquipmentCad(
    scope: ScopeUrl,
    typeId: string,
    filename: string,
    data: Blob | ArrayBuffer,
  ): Promise<{ cad_key: string }> {
    const r = await authedFetch(
      `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/equipment-types/${encodeURIComponent(typeId)}` +
        `/cad?filename=${encodeURIComponent(filename)}`,
      {
        method: "POST",
        headers: { "Content-Type": "application/octet-stream" },
        body: data,
      },
    );
    return jsonOrThrow<{ cad_key: string }>(
      r,
      `uploadEquipmentCad(${filename})`,
    );
  },

  /** Attach a CAD asset by copying an existing scope file. */
  async copyEquipmentCadFromScope(
    scope: ScopeUrl,
    typeId: string,
    sourceKey: string,
  ): Promise<{ cad_key: string }> {
    const r = await authedFetch(
      `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/equipment-types/${encodeURIComponent(typeId)}` +
        `/cad-from-scope`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ source_key: sourceKey }),
      },
    );
    return jsonOrThrow<{ cad_key: string }>(
      r,
      `copyEquipmentCadFromScope(${sourceKey})`,
    );
  },

  /** Enqueue bbox inference + preview render from the linked CAD asset (poll
   * via convertStatus; on done the doc bbox is updated + preview GLB exists). */
  async inferEquipmentBbox(
    scope: ScopeUrl,
    typeId: string,
  ): Promise<{ job_id: string; derived_key: string }> {
    const r = await authedFetch(
      `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/equipment-types/${encodeURIComponent(typeId)}` +
        `/infer-bbox`,
      { method: "POST" },
    );
    return jsonOrThrow<{ job_id: string; derived_key: string }>(
      r,
      `inferEquipmentBbox(${typeId})`,
    );
  },

  // ── System-template catalog (admin panel) ────────────────────────

  async listSystemTemplates(scope: ScopeUrl): Promise<SystemTemplateSummary[]> {
    const r = await authedFetch(
      `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/system-templates`,
    );
    const body = await jsonOrThrow<{
      system_templates: SystemTemplateSummary[];
    }>(r, `listSystemTemplates(${scope})`);
    return body.system_templates;
  },

  async createSystemTemplate(
    scope: ScopeUrl,
    name: string,
    slug?: string,
    description?: string,
  ): Promise<SystemTemplateDetail> {
    const r = await authedFetch(
      `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/system-templates`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, slug, description }),
      },
    );
    return jsonOrThrow<SystemTemplateDetail>(
      r,
      `createSystemTemplate(${name})`,
    );
  },

  async getSystemTemplate(
    scope: ScopeUrl,
    templateId: string,
  ): Promise<SystemTemplateDetail> {
    const r = await authedFetch(
      `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/system-templates/${encodeURIComponent(templateId)}`,
    );
    return jsonOrThrow<SystemTemplateDetail>(
      r,
      `getSystemTemplate(${templateId})`,
    );
  },

  async updateSystemTemplate(
    scope: ScopeUrl,
    templateId: string,
    fields: {
      name: string;
      slug?: string;
      description?: string | null;
      doc: SystemTemplateDoc;
    },
    baseRevision: number,
  ): Promise<{ id: string; revision: number }> {
    const r = await authedFetch(
      `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/system-templates/${encodeURIComponent(templateId)}`,
      {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...fields, base_revision: baseRevision }),
      },
    );
    return jsonOrThrow<{ id: string; revision: number }>(
      r,
      `updateSystemTemplate(${templateId})`,
    );
  },

  async deleteSystemTemplate(
    scope: ScopeUrl,
    templateId: string,
  ): Promise<void> {
    const r = await authedFetch(
      `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/system-templates/${encodeURIComponent(templateId)}`,
      { method: "DELETE" },
    );
    if (!r.ok)
      throw new ApiError(
        `deleteSystemTemplate(${templateId})`,
        r.status,
        await readDetail(r),
      );
  },

  // ── Procedural-engine registry ───────────────────────────────────

  async listProceduralEngines(
    scope: ScopeUrl,
  ): Promise<ProceduralEngineSummary[]> {
    const r = await authedFetch(
      `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/procedural-engines`,
    );
    const body = await jsonOrThrow<{
      procedural_engines: ProceduralEngineSummary[];
    }>(r, `listProceduralEngines(${scope})`);
    return body.procedural_engines;
  },

  /** Detailing engines for the Compile-settings "Detailing" dropdown (built-in ∪
   * worker-advertised). `none` (first, the default) = structural-only. */
  async listDetailingEngines(
    scope: ScopeUrl,
  ): Promise<DetailingEngineSummary[]> {
    const r = await authedFetch(
      `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/procedural-models/detailing-engines`,
    );
    const body = await jsonOrThrow<{
      detailing_engines: DetailingEngineSummary[];
    }>(r, `listDetailingEngines(${scope})`);
    return body.detailing_engines;
  },

  /** Resolve an engine to a browser-runnable descriptor for the in-browser
   * (Pyodide) compile: a built-in returns its slug; a kind:wheel engine returns
   * its module:callable entrypoint, the micropip deps and a presigned wheel URL
   * (when built — `ready`). A kind:server engine is not browser-runnable. */
  async resolveProceduralEngine(
    scope: ScopeUrl,
    engineId: string,
  ): Promise<ProceduralEngineResolved> {
    const r = await authedFetch(
      `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/procedural-engines/${encodeURIComponent(engineId)}/resolve`,
    );
    return jsonOrThrow<ProceduralEngineResolved>(
      r,
      `resolveProceduralEngine(${engineId})`,
    );
  },

  async createProceduralEngine(
    scope: ScopeUrl,
    name: string,
    slug?: string,
    description?: string,
  ): Promise<ProceduralEngineDetail> {
    const r = await authedFetch(
      `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/procedural-engines`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, slug, description }),
      },
    );
    return jsonOrThrow<ProceduralEngineDetail>(
      r,
      `createProceduralEngine(${name})`,
    );
  },

  async getProceduralEngine(
    scope: ScopeUrl,
    engineId: string,
  ): Promise<ProceduralEngineDetail> {
    const r = await authedFetch(
      `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/procedural-engines/${encodeURIComponent(engineId)}`,
    );
    return jsonOrThrow<ProceduralEngineDetail>(
      r,
      `getProceduralEngine(${engineId})`,
    );
  },

  async updateProceduralEngine(
    scope: ScopeUrl,
    engineId: string,
    fields: {
      name: string;
      slug?: string;
      description?: string | null;
      doc: ProceduralEngineDoc;
    },
    baseRevision: number,
  ): Promise<{ id: string; revision: number }> {
    const r = await authedFetch(
      `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/procedural-engines/${encodeURIComponent(engineId)}`,
      {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...fields, base_revision: baseRevision }),
      },
    );
    return jsonOrThrow<{ id: string; revision: number }>(
      r,
      `updateProceduralEngine(${engineId})`,
    );
  },

  async deleteProceduralEngine(
    scope: ScopeUrl,
    engineId: string,
  ): Promise<void> {
    const r = await authedFetch(
      `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/procedural-engines/${encodeURIComponent(engineId)}`,
      { method: "DELETE" },
    );
    if (!r.ok)
      throw new ApiError(
        `deleteProceduralEngine(${engineId})`,
        r.status,
        await readDetail(r),
      );
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
  async componentsSpecs(opts?: {
    scope?: ScopeUrl;
    branch?: string;
  }): Promise<ComponentSpecsResponse> {
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
  async componentsProfiles(
    category?: string,
  ): Promise<ComponentsProfilesResponse> {
    const url = category
      ? `${runtime.apiBase()}/components/profiles?category=${encodeURIComponent(category)}`
      : `${runtime.apiBase()}/components/profiles`;
    const r = await authedFetch(url);
    return jsonOrThrow<ComponentsProfilesResponse>(
      r,
      `componentsProfiles(${category ?? ""})`,
    );
  },

  /** Enqueue an on-demand component build for user-tweaked inputs.
   * Returns `{job_id, derived_key}`; poll status via convertStatus
   * and fetch the result GLB via getBlob(scope, derived_key) once
   * the job reports `done`. */
  async componentsBuild(
    payload: ComponentBuildPayload,
    opts?: { scope?: ScopeUrl },
  ): Promise<ComponentBuildResponse> {
    const url = opts?.scope
      ? `${runtime.apiBase()}/components/build?scope=${encodeURIComponent(opts.scope)}`
      : `${runtime.apiBase()}/components/build`;
    const r = await authedFetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    return jsonOrThrow<ComponentBuildResponse>(
      r,
      `componentsBuild(${payload.spec_name})`,
    );
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
    const body = await jsonOrThrow<{ jobs: AuditEntry[] }>(
      r,
      `myJobs(${scope})`,
    );
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
      { method: "POST" },
    );
    if (r.status === 404) return false;
    await jsonOrThrow<{ job_id: string; cancelled: boolean }>(
      r,
      `cancelMyJob(${jobId})`,
    );
    return true;
  },

  /** Server-side viable-target listing. The frontend mirrors this
   * mapping client-side too, but this lets us cross-check. */
  async convertTargets(
    scope: ScopeUrl,
    sourceKey: string,
  ): Promise<TargetFormat[]> {
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
  ): Promise<{ entries: AuditEntry[]; next_before_id: number | null }> {
    const params = new URLSearchParams();
    for (const [k, v] of Object.entries(filters)) {
      if (v !== undefined && v !== "" && v !== null) params.set(k, String(v));
    }
    const qs = params.toString();
    const url = `${runtime.apiBase()}/admin/audit${qs ? `?${qs}` : ""}`;
    const r = await authedFetch(url);
    return jsonOrThrow(r, "adminAudit");
  },

  /** Admin: aggregate counts for the audit Overview, under the same filter
   * the log uses. ``status`` is intentionally not sent — the summary shows how
   * a population splits across states, and the tiles are what set that filter,
   * so honouring it would zero every tile but the selected one. */
  async adminAuditSummary(filters: AuditFilters = {}): Promise<AuditSummary> {
    const params = new URLSearchParams();
    for (const [k, v] of Object.entries(filters)) {
      if (k === "status" || k === "limit" || k === "before_id") continue;
      if (v !== undefined && v !== "" && v !== null) params.set(k, String(v));
    }
    const qs = params.toString();
    const url = `${runtime.apiBase()}/admin/audit/summary${qs ? `?${qs}` : ""}`;
    const r = await authedFetch(url);
    return jsonOrThrow(r, "adminAuditSummary");
  },

  /** Admin: the captured package manifest ("pixi list") for a worker image
   * tag — linked from a convert audit row via its worker_image_tag. */
  async adminWorkerPackages(imageTag: string): Promise<{
    worker_image_tag: string;
    packages: WorkerPackage[];
    captured_at: string | null;
  }> {
    const url = `${runtime.apiBase()}/admin/worker-packages/${encodeURIComponent(imageTag)}`;
    const r = await authedFetch(url);
    return jsonOrThrow(r, "adminWorkerPackages");
  },

  /** Admin: list live regression corpora (admin-curated proprietary
   * source sets driving M3 audit sweeps). Archived rows hidden. */
  async adminCorporaList(): Promise<{ corpora: Corpus[] }> {
    const r = await authedFetch(`${runtime.apiBase()}/admin/corpora`);
    return jsonOrThrow(r, "adminCorporaList");
  },

  /** Admin: create a new corpus. ``slug`` is the human-readable id
   * embedded in scope tokens (``corpus:<slug>``). Storage prefix +
   * wire format both follow from it; 409 on duplicate live slug. */
  async adminCorpusCreate(body: {
    slug: string;
    name: string;
    description?: string | null;
  }): Promise<Corpus> {
    const r = await authedFetch(`${runtime.apiBase()}/admin/corpora`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    return jsonOrThrow(r, "adminCorpusCreate");
  },

  /** Admin: update a corpus's display name / description. The slug
   * is immutable (baked into the storage prefix + scope URLs). An
   * empty description clears it. */
  async adminCorpusUpdate(
    slug: string,
    body: { name: string; description: string | null },
  ): Promise<Corpus> {
    const r = await authedFetch(
      `${runtime.apiBase()}/admin/corpora/${encodeURIComponent(slug)}`,
      {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      },
    );
    return jsonOrThrow(r, "adminCorpusUpdate");
  },

  /** Admin: soft-delete a corpus by slug. Storage bytes survive —
   * the operator clears those out-of-band. The slug becomes
   * immediately available for reuse. */
  async adminCorpusArchive(slug: string): Promise<void> {
    const r = await authedFetch(
      `${runtime.apiBase()}/admin/corpora/${encodeURIComponent(slug)}`,
      { method: "DELETE" },
    );
    if (!r.ok) {
      throw new ApiError(
        `adminCorpusArchive(${slug})`,
        r.status,
        await readDetail(r),
      );
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
  async adminAuditRunCreate(body: {
    scope: ScopeUrl;
    worker_pool?: string | null;
    note?: string | null;
    force_rebuild?: boolean;
    validate_only?: boolean;
    auto_validate?: boolean;
  }): Promise<AuditRun> {
    const r = await authedFetch(`${runtime.apiBase()}/admin/audit/runs`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    return jsonOrThrow(r, "adminAuditRunCreate");
  },

  /** Admin: re-run a prior audit against the same scope / pool / settings.
   * The cells are re-enumerated from the scope at dispatch time, so the
   * re-run reflects the scope's current files. Returns the new run. */
  async adminAuditRunReDispatch(runId: string): Promise<AuditRun> {
    const r = await authedFetch(
      `${runtime.apiBase()}/admin/audit/runs/${encodeURIComponent(runId)}/re-dispatch`,
      { method: "POST" },
    );
    return jsonOrThrow(r, `adminAuditRunReDispatch(${runId})`);
  },

  /** Admin: re-run a single cell of a run in place (right-click → Rerun).
   * Enqueues one force-rebuild conversion for (key, target) against the run's
   * own scope/pool and reopens the run; the other cells are untouched.
   * Returns the (reopened) run. */
  async adminAuditRunRerunCell(
    runId: string,
    key: string,
    target: string,
  ): Promise<AuditRun> {
    const r = await authedFetch(
      `${runtime.apiBase()}/admin/audit/runs/${encodeURIComponent(runId)}/rerun-cell`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ key, target }),
      },
    );
    return jsonOrThrow(r, `adminAuditRunRerunCell(${runId})`);
  },

  /** Admin: append a validation (cross-format parity) pass to a finished
   * run — folded into the same run, not a new one. 409 if the run isn't
   * finished or has already been validated. Returns the (reopened) run. */
  async adminAuditRunValidate(runId: string): Promise<AuditRun> {
    const r = await authedFetch(
      `${runtime.apiBase()}/admin/audit/runs/${encodeURIComponent(runId)}/validate`,
      { method: "POST" },
    );
    return jsonOrThrow(r, `adminAuditRunValidate(${runId})`);
  },

  /** Admin: delete an audit run and its audit_log rows (parity cascades).
   * 409 if the run is still running — cancel it first. */
  async adminAuditRunDelete(runId: string): Promise<void> {
    const r = await authedFetch(
      `${runtime.apiBase()}/admin/audit/runs/${encodeURIComponent(runId)}`,
      { method: "DELETE" },
    );
    if (!r.ok) {
      throw new ApiError(
        `adminAuditRunDelete(${runId})`,
        r.status,
        await readDetail(r),
      );
    }
  },

  /** Admin: historic results for one (source key, target_format) cell across
   * every run, newest first. Backs the grid's right-click "show history". */
  async adminAuditCellHistory(
    key: string,
    target: string,
    limit = 50,
  ): Promise<{
    key: string;
    target_format: string;
    history: AuditCellHistoryRow[];
  }> {
    const params = new URLSearchParams({ key, target, limit: String(limit) });
    const r = await authedFetch(
      `${runtime.apiBase()}/admin/audit/cell-history?${params.toString()}`,
    );
    return jsonOrThrow(r, "adminAuditCellHistory");
  },

  /** Cell matrix for an audit run — drives the in-browser (WASM) sweep
   * executor. ``done`` flags cells that already have a terminal audit row
   * for this run, so a reload resumes instead of re-running them. */
  async adminAuditRunCells(runId: string): Promise<{
    run_id: string;
    scope: ScopeUrl;
    cells: Array<{ source_key: string; target_format: string; done: boolean }>;
  }> {
    const r = await authedFetch(
      `${runtime.apiBase()}/admin/audit/runs/${encodeURIComponent(runId)}/cells`,
    );
    return jsonOrThrow(r, "adminAuditRunCells");
  },

  /** Admin: ambient summary of currently-running audit sweeps.
   * Drives the bottom-right badge that links into the audit Runs
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
  }): Promise<{ runs: AuditRun[]; next_before_started_at: string | null }> {
    const params = new URLSearchParams();
    if (opts?.limit) params.set("limit", String(opts.limit));
    if (opts?.before_started_at)
      params.set("before_started_at", opts.before_started_at);
    const qs = params.toString();
    const url = `${runtime.apiBase()}/admin/audit/runs${qs ? `?${qs}` : ""}`;
    const r = await authedFetch(url);
    return jsonOrThrow(r, "adminAuditRunsList");
  },

  /** Admin: one run + every audit_log row tied to it. The job list
   * powers the per-cell grid view (files × targets) in the audit
   * panel. Returned in dispatch order (asc by audit_log.id) so
   * grid rendering is deterministic. */
  async adminAuditRunGet(
    runId: string,
  ): Promise<{ run: AuditRun; jobs: AuditRunJob[] }> {
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
      { method: "POST" },
    );
    return jsonOrThrow(r, `adminAuditRunCancel(${runId})`);
  },

  /** Admin: list live audit schedules (M4). Archived rows hidden;
   * the picker only ever wants currently-firing rows. */
  async adminAuditSchedulesList(): Promise<{ schedules: AuditSchedule[] }> {
    const r = await authedFetch(`${runtime.apiBase()}/admin/audit/schedules`);
    return jsonOrThrow(r, "adminAuditSchedulesList");
  },

  /** Admin: create a recurring schedule. ``cron_expr`` is validated
   * server-side via croniter — invalid expressions return 400 with
   * the croniter parse error in the body. */
  async adminAuditScheduleCreate(body: {
    name: string;
    cron_expr: string;
    scope: string;
    worker_pool?: string | null;
    enabled?: boolean;
  }): Promise<AuditSchedule> {
    const r = await authedFetch(`${runtime.apiBase()}/admin/audit/schedules`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
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
        headers: { "Content-Type": "application/json" },
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
      { method: "DELETE" },
    );
    if (!r.ok) {
      throw new ApiError(
        `adminAuditScheduleArchive(${scheduleId})`,
        r.status,
        await readDetail(r),
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
      { method: "POST" },
    );
    return jsonOrThrow(r, `adminAuditScheduleFireNow(${scheduleId})`);
  },

  /** Admin: read the configured issue-tracker target (M5). Tokens
   * never come back — only the env var name + a present/missing
   * flag for the serving replica. */
  async adminIssueTargetGet(): Promise<IssueTargetConfig> {
    const r = await authedFetch(
      `${runtime.apiBase()}/admin/audit/issue-target`,
    );
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
    const r = await authedFetch(
      `${runtime.apiBase()}/admin/audit/issue-target`,
      {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      },
    );
    return jsonOrThrow(r, "adminIssueTargetSet");
  },

  /** Admin: re-run the issue-bot sync for one finished audit run.
   * Clears the prior ``issue_bot_status`` and kicks an immediate
   * sync as a background task so the user gets quick feedback. */
  async adminAuditRunSyncIssues(runId: string): Promise<void> {
    const r = await authedFetch(
      `${runtime.apiBase()}/admin/audit/runs/${encodeURIComponent(runId)}/sync-issues`,
      { method: "POST" },
    );
    if (!r.ok) {
      throw new ApiError(
        `adminAuditRunSyncIssues(${runId})`,
        r.status,
        await readDetail(r),
      );
    }
  },

  /** Admin: re-run the issue-bot for ONE failed user conversion
   * (M5b). Mirrors adminAuditRunSyncIssues; the response is 202
   * + the row gets re-claimed by the bot's background task. */
  async adminAuditLogSyncIssue(auditId: number): Promise<void> {
    const r = await authedFetch(
      `${runtime.apiBase()}/admin/audit/${auditId}/sync-issue`,
      { method: "POST" },
    );
    if (!r.ok) {
      throw new ApiError(
        `adminAuditLogSyncIssue(${auditId})`,
        r.status,
        await readDetail(r),
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
    if (opts?.worker_image_tag)
      params.set("worker_image_tag", opts.worker_image_tag);
    const qs = params.toString();
    const url = `${runtime.apiBase()}/admin/audit/perf${qs ? `?${qs}` : ""}`;
    const r = await authedFetch(url);
    return jsonOrThrow(r, "adminPerfReport");
  },

  /** Admin: distinct worker_image_tag values seen in the perf
   * window, freshest first. Drives the PerformanceTab "Worker SHA"
   * picker — only tags with data behind them appear. */
  async adminPerfWorkers(since = 90): Promise<{
    workers: { tag: string; samples: number; last_seen: string | null }[];
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
    const r = await authedFetch(
      `${runtime.apiBase()}/admin/audit/perf/thresholds`,
    );
    return jsonOrThrow(r, "adminPerfThresholdsGet");
  },

  /** Admin: write threshold overrides. Pass ``null`` for a key to
   * clear the override (ship-default takes over). Unknown keys
   * 400 — we'd rather catch a typo than silently disable a signal. */
  async adminPerfThresholdsSet(
    body: Record<string, number | null>,
  ): Promise<PerfThresholdsResp> {
    const r = await authedFetch(
      `${runtime.apiBase()}/admin/audit/perf/thresholds`,
      {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      },
    );
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

  /** Record one browser model-load (``action='view'``) — the viewer's
   * opt-in load instrumentation posts this once a GLB is in the scene.
   * Best-effort: never throws into the load path. ``client_metrics`` is
   * the per-phase IO/network/CPU/GPU + payload + device breakdown. */
  async recordViewLoad(
    scope: ScopeUrl,
    body: {
      key: string;
      status?: "ok" | "error";
      duration_ms?: number | null;
      read_bytes?: number | null;
      write_bytes?: number | null;
      peak_rss_kb?: number | null;
      error?: string | null;
      traceback?: string | null;
      client_metrics?: Record<string, unknown> | null;
    },
  ): Promise<void> {
    try {
      await authedFetch(
        `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/audit/view`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        },
      );
    } catch (e) {
      // Metrics must never disrupt the session.
      console.debug("recordViewLoad failed (ignored)", e);
    }
  },

  /** Record one steady-state render-performance window
   * (``action='render'``). Same best-effort contract as
   * ``recordViewLoad``. */
  async recordRenderProfile(
    scope: ScopeUrl,
    body: {
      key: string;
      duration_ms?: number | null;
      client_metrics?: Record<string, unknown> | null;
    },
  ): Promise<void> {
    try {
      await authedFetch(
        `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/audit/view`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          // The ingest endpoint stores any action via client_metrics;
          // render rows are marked by client_metrics.kind === "render"
          // and the backend routes them to action='render'.
          body: JSON.stringify(body),
        },
      );
    } catch (e) {
      console.debug("recordRenderProfile failed (ignored)", e);
    }
  },

  /** Admin: per-file browser model-load perf snapshot. One cell per
   * GLB with p50/p95 of every load phase + a dominant-bottleneck
   * label (io / network / cpu / gpu). */
  async adminFrontendLoads(since = 30): Promise<{
    cells: Array<Record<string, number | string | null>>;
    since_days: number;
    generated_at: string;
  }> {
    const r = await authedFetch(
      `${runtime.apiBase()}/admin/audit/frontend-loads?since=${since}`,
    );
    return jsonOrThrow(r, "adminFrontendLoads");
  },

  /** Admin: function-level hotspots (summed JS Self-Profiling self-time
   * per TS/WASM frame) across browser loads, optionally one ``key``. */
  async adminFrontendLoadHotspots(opts: {
    key?: string;
    since?: number;
    limit?: number;
    kind?: "view" | "render";
  }): Promise<{
    functions: Array<{
      fn: string;
      samples: number;
      self_ms_sum: number | null;
      self_ms_avg: number | null;
      total_ms_max: number | null;
      is_wasm: boolean;
    }>;
    loads_in_window: number;
    key: string | null;
    kind: string;
    since_days: number;
  }> {
    const params = new URLSearchParams();
    if (opts.key) params.set("key", opts.key);
    if (opts.since != null) params.set("since", String(opts.since));
    if (opts.limit != null) params.set("limit", String(opts.limit));
    if (opts.kind) params.set("kind", opts.kind);
    const qs = params.toString();
    const r = await authedFetch(
      `${runtime.apiBase()}/admin/audit/frontend-loads/hotspots${qs ? `?${qs}` : ""}`,
    );
    return jsonOrThrow(r, "adminFrontendLoadHotspots");
  },

  /** Admin: the client_metrics payload for one browser view/render
   * audit row — per-phase split + device + per-function frames. Backs
   * the audit-log detail "Client" tab. */
  async adminAuditClientMetrics(id: number): Promise<{
    audit_id: number;
    client_metrics: Record<string, unknown> | null;
  }> {
    const r = await authedFetch(
      `${runtime.apiBase()}/admin/audit/${id}/client-metrics`,
    );
    return jsonOrThrow(r, "adminAuditClientMetrics");
  },

  /** Admin: per-file steady-state render-performance snapshot
   * (``action='render'``). */
  async adminRenderProfiles(since = 30): Promise<{
    cells: Array<Record<string, number | string | null>>;
    since_days: number;
    generated_at: string;
  }> {
    const r = await authedFetch(
      `${runtime.apiBase()}/admin/audit/render?since=${since}`,
    );
    return jsonOrThrow(r, "adminRenderProfiles");
  },

  /** Admin: kick off a background sweep that scans the scope for
   * gzip-compressible source files (.ifc / .step / .sif / etc.)
   * whose stored bytes aren't gzipped, and rewrites each with
   * Content-Encoding: gzip. Returns 202 immediately — poll
   * ``adminCompressionStatus`` for progress. */
  async adminStartCompressionSweep(scope: ScopeUrl): Promise<void> {
    const r = await authedFetch(
      `${runtime.apiBase()}/admin/storage/${encodeURIComponent(scope)}/compress-uncompressed`,
      { method: "POST" },
    );
    if (!r.ok) {
      throw new ApiError(
        `adminStartCompressionSweep(${scope})`,
        r.status,
        await readDetail(r),
      );
    }
  },

  /** Admin: snapshot of the in-flight + recently-completed
   * compression sweeps, keyed by scope. */
  async adminCompressionStatus(): Promise<{
    scopes: Record<string, CompressionSweepState>;
  }> {
    const r = await authedFetch(
      `${runtime.apiBase()}/admin/storage/compression-status`,
    );
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

  /** Admin: drop every currently-offline worker registry entry (a live pod re-registers within a
   * heartbeat). Returns the number pruned. */
  async adminPruneWorkers(): Promise<{ pruned: number }> {
    const r = await authedFetch(`${runtime.apiBase()}/admin/workers/prune`, {
      method: "POST",
    });
    return jsonOrThrow(r, "adminPruneWorkers");
  },

  /** Admin: fetch a conversion's captured stdout/stderr log (the log_key blob) as text. Throws
   * ApiError(404) when the row has no log attached (predates log capture, or no conversion ran). */
  async adminGetAuditLog(auditId: number): Promise<string> {
    const r = await authedFetch(
      `${runtime.apiBase()}/admin/audit/${auditId}/log`,
    );
    if (!r.ok) {
      throw new ApiError(
        `adminGetAuditLog(${auditId})`,
        r.status,
        await readDetail(r),
      );
    }
    return r.text();
  },

  /** Read a key from the publicly-readable `public.` settings namespace. Any
   * authenticated user; 403 for a key outside that namespace. Use this (not
   * `adminGetSetting`) for configuration a non-admin's UI has to see. Writes
   * remain admin-only — there is no public setter. */
  async getPublicSetting(key: string): Promise<string | null> {
    const r = await authedFetch(
      `${runtime.apiBase()}/settings/${encodeURIComponent(key)}`,
    );
    const body = await jsonOrThrow<{ key: string; value: string | null }>(
      r,
      `getPublicSetting(${key})`,
    );
    return body.value;
  },

  /** Admin: read a key from app_settings. Value is null when unset. */
  async adminGetSetting(key: string): Promise<string | null> {
    const r = await authedFetch(
      `${runtime.apiBase()}/admin/settings/${encodeURIComponent(key)}`,
    );
    const body = await jsonOrThrow<{ key: string; value: string | null }>(
      r,
      `adminGetSetting(${key})`,
    );
    return body.value;
  },

  /** Admin: set a key in app_settings. Stringified server-side; the
   * caller is responsible for the encoding (e.g. "true"/"false"). */
  async adminSetSetting(key: string, value: string): Promise<void> {
    const r = await authedFetch(
      `${runtime.apiBase()}/admin/settings/${encodeURIComponent(key)}`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ value }),
      },
    );
    if (!r.ok) {
      throw new ApiError(
        `adminSetSetting(${key})`,
        r.status,
        await readDetail(r),
      );
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
  async adminMintCliToken(): Promise<{ token: string; expires_at: number }> {
    const r = await authedFetch(`${runtime.apiBase()}/admin/auth/cli-token`, {
      method: "POST",
    });
    return jsonOrThrow(r, "adminMintCliToken");
  },

  /** Revoke every previously-minted CLI token for the current user
   * by bumping the per-user cutoff. The OIDC bearer used for this
   * request stays valid. */
  async adminRevokeCliTokens(): Promise<{ revoked_at: number }> {
    const r = await authedFetch(
      `${runtime.apiBase()}/admin/auth/cli-token/revoke`,
      { method: "POST" },
    );
    return jsonOrThrow(r, "adminRevokeCliTokens");
  },

  /** Trigger the original-source download for an audit row. Used by
   * the local repro pixi tasks but also handy for one-off debugging
   * straight from the admin panel. */
  async adminDownloadAuditSource(
    auditId: number,
    suggestedName: string,
  ): Promise<void> {
    const r = await authedFetch(
      `${runtime.apiBase()}/admin/audit/${auditId}/source`,
    );
    if (!r.ok) {
      throw new ApiError(
        `adminDownloadAuditSource(${auditId})`,
        r.status,
        await readDetail(r),
      );
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
  async adminDownloadProfile(
    auditId: number,
    suggestedName: string,
  ): Promise<void> {
    const r = await authedFetch(this.adminProfileUrl(auditId));
    if (!r.ok) {
      throw new ApiError(
        `adminDownloadProfile(${auditId})`,
        r.status,
        await readDetail(r),
      );
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
  async adminProfileStats(
    auditId: number,
    limit = 500,
  ): Promise<ProfileStatsResp> {
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
  async adminClearMetrics(): Promise<{
    rows_cleared: number;
    profiles_deleted: number;
    errors: string[];
  }> {
    const r = await authedFetch(`${runtime.apiBase()}/admin/audit/metrics`, {
      method: "DELETE",
    });
    return jsonOrThrow(r, "adminClearMetrics");
  },

  async adminListProjects(): Promise<AdminProject[]> {
    const r = await authedFetch(`${runtime.apiBase()}/admin/projects`);
    const body = await jsonOrThrow<{ projects: AdminProject[] }>(
      r,
      "adminListProjects",
    );
    return body.projects;
  },

  async adminCreateProject(slug: string, name: string): Promise<AdminProject> {
    const r = await authedFetch(`${runtime.apiBase()}/admin/projects`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ slug, name }),
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
  ): Promise<{ user_sub: string; token: string; expires_at: number }> {
    const r = await authedFetch(
      `${runtime.apiBase()}/admin/projects/${encodeURIComponent(projectId)}/ci-bot`,
      { method: "POST" },
    );
    return jsonOrThrow(r, "adminProvisionCiBot");
  },

  async adminArchiveProject(projectId: string): Promise<void> {
    const r = await authedFetch(
      `${runtime.apiBase()}/admin/projects/${encodeURIComponent(projectId)}`,
      { method: "DELETE" },
    );
    if (!r.ok && r.status !== 204) {
      throw new ApiError(
        `adminArchiveProject failed: ${r.status}`,
        r.status,
        await readDetail(r),
      );
    }
  },

  async adminListMembers(projectId: string): Promise<ProjectMember[]> {
    const r = await authedFetch(
      `${runtime.apiBase()}/admin/projects/${encodeURIComponent(projectId)}/members`,
    );
    const body = await jsonOrThrow<{ members: ProjectMember[] }>(
      r,
      "adminListMembers",
    );
    return body.members;
  },

  async adminAddMember(
    projectId: string,
    userSub: string,
    role: string = "member",
  ): Promise<{ user_sub: string; role: string; added: boolean }> {
    const r = await authedFetch(
      `${runtime.apiBase()}/admin/projects/${encodeURIComponent(projectId)}/members`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ user_sub: userSub, role }),
      },
    );
    return jsonOrThrow(r, "adminAddMember");
  },

  /** Admin: enriched per-scope listing (format, last_modified,
   * derived products). Same scope check as the user-facing /files
   * endpoint — admins still need scope access. */
  async adminListStorage(
    scope: ScopeUrl,
    opts?: { signal?: AbortSignal },
  ): Promise<AdminFileEntry[]> {
    const r = await authedFetch(
      `${runtime.apiBase()}/admin/scopes/${encodeURIComponent(scope)}/files`,
      { signal: opts?.signal },
    );
    const body = await jsonOrThrow<{ files: AdminFileEntry[] }>(
      r,
      "adminListStorage",
    );
    return body.files;
  },

  /** Admin: delete a source (and all its derived blobs) or a single
   * derived blob. Returns the list of keys actually removed. */
  async adminDeleteBlob(
    scope: ScopeUrl,
    key: string,
  ): Promise<{ deleted: string[]; errors?: string[] }> {
    const r = await authedFetch(
      `${runtime.apiBase()}/admin/scopes/${encodeURIComponent(scope)}/blobs/${encodeURIComponent(key)}`,
      { method: "DELETE" },
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
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ keys, folder }),
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
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ old_key: oldKey, new_key: newKey }),
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
    copied: Array<{ key: string }>;
    skipped: Array<{ key: string; reason: string }>;
    failed: Array<{ key: string; reason: string }>;
  }> {
    const r = await authedFetch(
      `${runtime.apiBase()}/admin/scopes/${encodeURIComponent(dstScope)}/keys/copy-from`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ src_scope: srcScope, keys }),
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
    const failedAll: Array<{ key: string; reason: string }> = [];
    // Sequential not parallel: each call mutates the scope's keyset
    // on the server; concurrent calls would race on collision
    // detection.
    for (const [dest, keys] of groups) {
      const r = await this.adminMoveKeysToFolder(scope, keys, dest);
      movedAll.push(...r.moved);
      failedAll.push(...r.failed);
    }
    return { moved: movedAll, failed: failedAll };
  },

  async adminRemoveMember(projectId: string, userSub: string): Promise<void> {
    const r = await authedFetch(
      `${runtime.apiBase()}/admin/projects/${encodeURIComponent(projectId)}` +
        `/members/${encodeURIComponent(userSub)}`,
      { method: "DELETE" },
    );
    if (!r.ok && r.status !== 204) {
      throw new ApiError(
        `adminRemoveMember failed: ${r.status}`,
        r.status,
        await readDetail(r),
      );
    }
  },
};

export { ApiError };
