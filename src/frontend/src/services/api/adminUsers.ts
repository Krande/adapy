// Admin user directory: every known principal, with its project memberships
// and what the server recorded from its OIDC token. Read-only — membership is
// edited per project (adminProjects.ts).

import { runtime } from "@/runtime/config";

import { authedFetch, jsonOrThrow } from "./client";

export interface AdminUserProject {
  id: string;
  slug: string;
  name: string;
  role: string;
  added_at: string | null;
  archived: boolean;
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
}

export const adminUsersApi = {
  async adminListUsers(): Promise<AdminUser[]> {
    const r = await authedFetch(`${runtime.apiBase()}/admin/users`);
    const body = await jsonOrThrow<{ users: AdminUser[] }>(r, "adminListUsers");
    return body.users;
  },
};
