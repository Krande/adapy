// System-template catalog: named piping/duct/cable/electrical presets a
// scope's cellbuilder can assign to a routed system.

import { runtime } from "@/runtime/config";

import { ApiError, authedFetch, jsonOrThrow, readDetail, type ScopeUrl } from "./client";

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

export const systemTemplatesApi = {
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
};
