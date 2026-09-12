// Admin corpora: the named collections an audit run can be scoped to.

import { runtime } from "@/runtime/config";

import { ApiError, authedFetch, jsonOrThrow, readDetail } from "./client";

// One admin-curated regression corpus. Returned by /admin/corpora
// endpoints. Wire format on its scope is ``corpus:<slug>``; the
// storage layer uses ``corpus/<slug>/`` as the bucket prefix.
export interface Corpus {
  id: string;
  slug: string;
  name: string;
  description: string | null;
  created_at: string | null;
  created_by: string | null;
  archived_at: string | null;
}

export const adminCorpusApi = {
  /** Admin: list live regression corpora (admin-curated proprietary
   * source sets driving M3 audit sweeps). Archived rows hidden. */
  async adminCorporaList(): Promise<{ corpora: Corpus[] }> {
    const r = await authedFetch(`${runtime.apiBase()}/admin/corpora`);
    return jsonOrThrow(r, "adminCorporaList");
  },

  /** Admin: create a new corpus. ``slug`` is the human-readable id
   * embedded in scope tokens (``corpus:<slug>``). Storage prefix +
   * wire format both follow from it; 409 on duplicate live slug. */
  async adminCorpusCreate(body: {
    slug: string;
    name: string;
    description?: string | null;
  }): Promise<Corpus> {
    const r = await authedFetch(`${runtime.apiBase()}/admin/corpora`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    return jsonOrThrow(r, "adminCorpusCreate");
  },

  /** Admin: update a corpus's display name / description. The slug
   * is immutable (baked into the storage prefix + scope URLs). An
   * empty description clears it. */
  async adminCorpusUpdate(
    slug: string,
    body: { name: string; description: string | null },
  ): Promise<Corpus> {
    const r = await authedFetch(
      `${runtime.apiBase()}/admin/corpora/${encodeURIComponent(slug)}`,
      {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      },
    );
    return jsonOrThrow(r, "adminCorpusUpdate");
  },

  /** Admin: soft-delete a corpus by slug. Storage bytes survive —
   * the operator clears those out-of-band. The slug becomes
   * immediately available for reuse. */
  async adminCorpusArchive(slug: string): Promise<void> {
    const r = await authedFetch(
      `${runtime.apiBase()}/admin/corpora/${encodeURIComponent(slug)}`,
      { method: "DELETE" },
    );
    if (!r.ok) {
      throw new ApiError(
        `adminCorpusArchive(${slug})`,
        r.status,
        await readDetail(r),
      );
    }
  },
};
