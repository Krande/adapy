// Procedural type catalogs: equipment/system/cell/opening type options,
// design rulesets, blueprints, procedural engines and detailing engines —
// everything the cellbuilder's dropdowns and the Detailing tab render from.

import { runtime } from "@/runtime/config";

import { ApiError, authedFetch, jsonOrThrow, readDetail, type ScopeUrl } from "./client";
import type { PortCategory, PortDirection } from "./equipmentTypes";
import type { SystemTemplateType } from "./systemTemplates";

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

export const proceduralCatalogApi = {
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
};
