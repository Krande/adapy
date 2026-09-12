// Key/value app settings: a public read plus the admin get/set pair.

import { runtime } from "@/runtime/config";

import { ApiError, authedFetch, jsonOrThrow, readDetail } from "./client";

export const settingsApi = {
  /** Read a key from the publicly-readable `public.` settings namespace. Any
   * authenticated user; 403 for a key outside that namespace. Use this (not
   * `adminGetSetting`) for configuration a non-admin's UI has to see. Writes
   * remain admin-only — there is no public setter. */
  async getPublicSetting(key: string): Promise<string | null> {
    const r = await authedFetch(
      `${runtime.apiBase()}/settings/${encodeURIComponent(key)}`,
    );
    const body = await jsonOrThrow<{ key: string; value: string | null }>(
      r,
      `getPublicSetting(${key})`,
    );
    return body.value;
  },

  /** Admin: read a key from app_settings. Value is null when unset. */
  async adminGetSetting(key: string): Promise<string | null> {
    const r = await authedFetch(
      `${runtime.apiBase()}/admin/settings/${encodeURIComponent(key)}`,
    );
    const body = await jsonOrThrow<{ key: string; value: string | null }>(
      r,
      `adminGetSetting(${key})`,
    );
    return body.value;
  },

  /** Admin: set a key in app_settings. Stringified server-side; the
   * caller is responsible for the encoding (e.g. "true"/"false"). */
  async adminSetSetting(key: string, value: string): Promise<void> {
    const r = await authedFetch(
      `${runtime.apiBase()}/admin/settings/${encodeURIComponent(key)}`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ value }),
      },
    );
    if (!r.ok) {
      throw new ApiError(
        `adminSetSetting(${key})`,
        r.status,
        await readDetail(r),
      );
    }
  },
};
