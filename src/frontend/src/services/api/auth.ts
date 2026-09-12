// Auth: the /me endpoint that resolves the caller's identity + scopes.

import { runtime } from "@/runtime/config";

import { authedFetch, jsonOrThrow } from "./client";

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

export const authApi = {
  /** Bootstrap the SPA's identity + available scopes. */
  async me(): Promise<MeResponse> {
    const r = await authedFetch(`${runtime.apiBase()}/me`);
    return jsonOrThrow<MeResponse>(r, "me");
  },
};
