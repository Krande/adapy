// Scope-gated plugins: which are turned on for the caller in a scope, and the admin's switches per scope
// and per user (`ada/comms/rest/routes/plugin_access.py`). What the switches mean is in
// `@/plugins/pluginAccess`.

import { runtime } from "@/runtime/config";
import type { PluginAccessAnswer } from "@/plugins/pluginAccess";

import { authedFetch, jsonOrThrow, type ScopeUrl } from "./client";

/** One switch: a plugin (its gate id) turned on or off for a scope (a storage prefix) or a user (a sub). */
export interface PluginAccessRow {
  id: string;
  principal_kind: "scope" | "user";
  principal: string;
  plugin_id: string;
  enabled: boolean;
  granted_at: string | null;
  granted_by: string | null;
  note: string | null;
}

/** A gate the server knows of, and where it saw it (`simulation_provider:<id>`, `plugin:<id>`,
 *  `binding:<kind>:<id>`, `switch`). */
export interface PluginAccessGate {
  id: string;
  sources: string[];
}

export interface PluginAccessRef {
  principal_kind: "scope" | "user";
  principal: string;
  plugin_id: string;
}

function json(method: string, body: unknown): RequestInit {
  return { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
}

export const pluginAccessApi = {
  /** The gated plugins turned on for the signed-in user in `scope`. */
  async scopePluginAccess(scope: ScopeUrl): Promise<PluginAccessAnswer> {
    const r = await authedFetch(`${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/plugin-access`);
    return jsonOrThrow<PluginAccessAnswer>(r, "scopePluginAccess");
  },

  /** Admin: every switch, and every gate the server knows of. */
  async adminPluginAccess(): Promise<{ rows: PluginAccessRow[]; gates: PluginAccessGate[] }> {
    const r = await authedFetch(`${runtime.apiBase()}/admin/plugin-access`);
    return jsonOrThrow(r, "adminPluginAccess");
  },

  /** Admin: turn a plugin on or off for a scope or a user. */
  async adminSetPluginAccess(body: PluginAccessRef & { enabled: boolean; note?: string }): Promise<{ row: PluginAccessRow }> {
    const r = await authedFetch(`${runtime.apiBase()}/admin/plugin-access`, json("PUT", body));
    return jsonOrThrow(r, "adminSetPluginAccess");
  },

  /** Admin: remove a switch (back to off, or for a user to what the scope says). */
  async adminClearPluginAccess(body: PluginAccessRef): Promise<{ removed: boolean }> {
    const r = await authedFetch(`${runtime.apiBase()}/admin/plugin-access`, json("DELETE", body));
    return jsonOrThrow(r, "adminClearPluginAccess");
  },
};
