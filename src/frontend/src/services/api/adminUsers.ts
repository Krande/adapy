// Admin user directory: every known principal, with its project memberships
// and what the server recorded from its OIDC token. Read-only — membership is
// edited per project (adminProjects.ts).

import { runtime } from "@/runtime/config";
import type {
  ExecEngine,
  ExecOperation,
  ExecutionPolicyDoc,
} from "@/services/compute/executionPolicyModel";

import { authedFetch, jsonOrThrow } from "./client";

/** `GET/PUT /api/admin/execution-policy`. `policy` is the completed global
 * document; `stored` false = the built-in defaults are in force. */
export interface AdminExecutionPolicy {
  policy: ExecutionPolicyDoc;
  stored: boolean;
  updated_by: string | null;
  updated_at: string | null;
  operations: ExecOperation[];
  engines: ExecEngine[];
  defaults: ExecutionPolicyDoc;
}

/** `GET/PUT /api/admin/users/{sub}/execution-policy`. `override` is `{}` when the
 * user has none; `effective` is what the user runs under. */
export interface AdminUserExecutionPolicy {
  user_sub: string;
  override: ExecutionPolicyDoc;
  effective: ExecutionPolicyDoc;
  updated_by: string | null;
  updated_at: string | null;
  operations: ExecOperation[];
  engines: ExecEngine[];
  defaults: ExecutionPolicyDoc;
}

async function putJson<T>(url: string, body: unknown, what: string): Promise<T> {
  const r = await authedFetch(url, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return jsonOrThrow<T>(r, what);
}

export interface AdminUserProject {
  id: string;
  slug: string;
  name: string;
  role: string;
  added_at: string | null;
  archived: boolean;
}

export interface AdminUserWorkerAccess {
  enabled: boolean;
  allowed_capabilities: string[];
  updated_by: string | null;
  updated_at: string | null;
}

export interface AdminUser {
  /** The OIDC `sub` claim — the durable id every audit row and membership
   * references. Never the email, which can change. */
  sub: string;
  email: string | null;
  display_name: string | null;
  last_seen_at: string | null;
  /** When the row appeared. Null for users recorded before the server
   * started keeping it. */
  created_at: string | null;
  /** From the token at the last sign-in. Null = not recorded yet, which is
   * NOT the same as "not an admin". */
  is_admin: boolean | null;
  groups: string[] | null;
  first_activity_at: string | null;
  last_activity_at: string | null;
  projects: AdminUserProject[];
  worker_access?: AdminUserWorkerAccess;
}

export const adminUsersApi = {
  async adminListUsers(): Promise<AdminUser[]> {
    const r = await authedFetch(`${runtime.apiBase()}/admin/users`);
    const body = await jsonOrThrow<{ users: AdminUser[] }>(r, "adminListUsers");
    return body.users;
  },

  async adminGetUserWorkerAccess(
    userSub: string,
  ): Promise<AdminUserWorkerAccess & { user_sub: string }> {
    const r = await authedFetch(
      `${runtime.apiBase()}/admin/users/${encodeURIComponent(userSub)}/worker-access`,
    );
    return jsonOrThrow(r, "adminGetUserWorkerAccess");
  },

  async adminUpdateUserWorkerAccess(
    userSub: string,
    policy: { enabled: boolean; allowed_capabilities: string[] },
  ): Promise<AdminUserWorkerAccess & { user_sub: string }> {
    const r = await authedFetch(
      `${runtime.apiBase()}/admin/users/${encodeURIComponent(userSub)}/worker-access`,
      {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(policy),
      },
    );
    return jsonOrThrow(r, "adminUpdateUserWorkerAccess");
  },

  async adminGetExecutionPolicy(): Promise<AdminExecutionPolicy> {
    const r = await authedFetch(`${runtime.apiBase()}/admin/execution-policy`);
    return jsonOrThrow(r, "adminGetExecutionPolicy");
  },

  /** Replace the global policy; `null` resets it to the built-in defaults. */
  async adminUpdateExecutionPolicy(policy: ExecutionPolicyDoc | null): Promise<AdminExecutionPolicy> {
    return putJson(`${runtime.apiBase()}/admin/execution-policy`, { policy }, "adminUpdateExecutionPolicy");
  },

  async adminGetUserExecutionPolicy(userSub: string): Promise<AdminUserExecutionPolicy> {
    const r = await authedFetch(
      `${runtime.apiBase()}/admin/users/${encodeURIComponent(userSub)}/execution-policy`,
    );
    return jsonOrThrow(r, "adminGetUserExecutionPolicy");
  },

  /** Replace a user's override; `null` or `{}` clears it. */
  async adminUpdateUserExecutionPolicy(
    userSub: string,
    override: ExecutionPolicyDoc | null,
  ): Promise<AdminUserExecutionPolicy> {
    return putJson(
      `${runtime.apiBase()}/admin/users/${encodeURIComponent(userSub)}/execution-policy`,
      { policy: override },
      "adminUpdateUserExecutionPolicy",
    );
  },
};
