// Component-spec catalog: the manifest of buildable component specs
// (per branch/profile) and the build dispatch endpoint.

import { runtime } from "@/runtime/config";

import { authedFetch, jsonOrThrow, type ScopeUrl } from "./client";

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

export const componentsApi = {
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
};
