// Admin projects: project CRUD, membership, CI-bot provisioning, job
// cancellation, and the CLI token pair issued for scripted access.

import { runtime } from "@/runtime/config";

import { ApiError, authedFetch, jsonOrThrow, readDetail } from "./client";

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

export const adminProjectsApi = {
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

  /** Provision (or rotate the token of) a synthetic CI bot user for a
   * project. Returns the bearer exactly once — the server does not
   * persist it. Re-calling rotates: the per-user revoke cutoff is
   * bumped before the new token is minted, so any tokens issued
   * previously to that bot stop validating.
   *
   * `name` gives the project more than one bot — `ci:<slug>:<name>`
   * instead of `ci:<slug>`. Without it the subject is unchanged, so
   * an existing bot keeps its identity and its tokens. Use a name per
   * consumer: the revoke cutoff is stored per subject, so consumers
   * sharing one bot cannot be rotated independently, and every audit
   * row reads the same subject whichever of them acted. */
  async adminProvisionCiBot(
    projectId: string,
    name?: string,
  ): Promise<{ user_sub: string; token: string; expires_at: number }> {
    const r = await authedFetch(
      `${runtime.apiBase()}/admin/projects/${encodeURIComponent(projectId)}/ci-bot`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(name ? { name } : {}),
      },
    );
    return jsonOrThrow(r, "adminProvisionCiBot");
  },

  /** Invalidate a CI bot's tokens without minting a replacement.
   *
   * What you want for a leaked credential or a retired consumer:
   * rotating would hand you a fresh secret you did not ask for and
   * leave the bot able to act. The bot stays a project member — remove
   * it separately, so its audit history still resolves to a named
   * principal. */
  async adminRevokeCiBot(
    projectId: string,
    name?: string,
  ): Promise<{ user_sub: string; revoked_at: number }> {
    const r = await authedFetch(
      `${runtime.apiBase()}/admin/projects/${encodeURIComponent(projectId)}/ci-bot/revoke`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(name ? { name } : {}),
      },
    );
    return jsonOrThrow(r, "adminRevokeCiBot");
  },

  /** Cancel and clear any job, whoever started it. Admin only.
   *
   * For a job nothing will ever finish — queued against a capability no
   * live worker serves, or left behind by a retired pool. The user-facing
   * my-jobs cancel filters on the job's owner, so an operator cleaning up
   * after someone else (or after a pool) cannot use it.
   *
   * `cancelled` and `purged` are independent: the audit row and the KV
   * entry can be stuck separately, and a job can need clearing from either
   * or both. */
  async adminCancelJob(
    jobId: string,
  ): Promise<{ job_id: string; cancelled: boolean; purged: boolean }> {
    const r = await authedFetch(
      `${runtime.apiBase()}/admin/jobs/${encodeURIComponent(jobId)}/cancel`,
      { method: "POST" },
    );
    return jsonOrThrow(r, "adminCancelJob");
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
