// FEA result inventory: the manifest (steps/fields) behind the Fields
// panel, plus the artefact sidecar (history CSVs etc.) some workers write
// alongside a result conversion.

import { runtime } from "@/runtime/config";

import type { FemConcepts } from "@/extensions/design_and_analysis_extension";
import { fetchFeaManifest, fetchResultMeta } from "@/services/feaManifestPoll";
import { authedFetch, authHeader, jsonOrThrow, type ScopeUrl } from "./client";
import { conversionApi } from "./conversion";

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
  /** Result-case name, when the reader knows one -- a Sesam deck names its
   *  cases (TDRESREF), and "lcc2" identifies a step in a way "10" does not. */
  name?: string;
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
  /** Model input data (thickness, material, section) painted like a result.
   *  Results pickers hide the category; an inspect/properties panel lists it. */
  | "property"
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
  /** Unit aligned with each component. Required when one field mixes
   * dimensions (for example beam forces and moments). */
  component_units?: string[];
  /** Categorical fields only (a material id, a section id): what each stored
   * numeric value means, keyed by the value's string form. */
  value_labels?: Record<string, string>;
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
  /** Freshness stamp of the bake that produced this tree (not the manifest
   * FORMAT version above). The server compares it against its expected value
   * and re-bakes older trees; the frontend never needs to read it. */
  bake_version?: number;
  src: string;
  mesh: {
    url: string;
    n_points: number;
    n_cells: number;
    /** Solver node ids aligned to the points array — what a node-number
     * label prints. Omitted when the reader has no identifiers; never
     * fall back to row indices, which lie on renumbered decks. */
    node_labels?: number[];
    /** Optional sidecar carrying deduped per-element edge index
     * pairs. When present, the frontend overlays them as a
     * THREE.LineSegments sharing the mesh's position attribute
     * so deformation drives both surface and edges. */
    edges_url?: string;
    /** The subset of ``edges_url`` belonging to LINE elements, same format.
     *  Absent on a model with no beams, and on any bake older than the split.
     *  Drawn in its own colour, and removed from ``edges_url`` before that is
     *  drawn, so no edge is painted twice. */
    line_edges_url?: string;
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

export const feaApi = {
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
      convertStatus: (jobId) => conversionApi.convertStatus(jobId),
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
      convertStatus: (jobId) => conversionApi.convertStatus(jobId),
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
};
