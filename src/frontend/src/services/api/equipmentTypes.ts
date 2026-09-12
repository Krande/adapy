// Equipment-type catalog: named box+ports+CAD entries a scope's cellbuilder
// can place, plus their linked CAD assets.

import { runtime } from "@/runtime/config";

import { ApiError, authedFetch, jsonOrThrow, readDetail, type ScopeUrl } from "./client";

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

export const equipmentTypesApi = {
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
};
