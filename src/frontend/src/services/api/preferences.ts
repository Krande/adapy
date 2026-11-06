// The caller's own preference documents (`/api/me/preferences/{key}`): one
// JSON value per key, kept per user on the server.

import { runtime } from "@/runtime/config";

import { ApiError, authedFetch, jsonOrThrow, readDetail } from "./client";

const url = (key: string) => `${runtime.apiBase()}/me/preferences/${encodeURIComponent(key)}`;

export const preferencesApi = {
  /** The stored value, or null when the user has none under `key`. 503 without a database. */
  async getPreference(key: string): Promise<unknown> {
    const body = await jsonOrThrow<{ key: string; value: unknown }>(await authedFetch(url(key)), `getPreference(${key})`);
    return body.value ?? null;
  },

  /** Replace the user's value under `key`; null removes it. */
  async setPreference(key: string, value: unknown, opts: { keepalive?: boolean } = {}): Promise<void> {
    const r = await authedFetch(url(key), {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ value }),
      keepalive: opts.keepalive,
    });
    if (!r.ok) throw new ApiError(`setPreference(${key})`, r.status, await readDetail(r));
  },
};
