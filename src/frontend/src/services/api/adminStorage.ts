// Admin storage: cross-scope blob listing/delete/move/rename/copy, the
// admin twin of services/api/files.ts (same wire shapes, admin-only
// scope access).

import { runtime } from "@/runtime/config";

import {
  authedFetch,
  groupKeysByRelativeParent,
  jsonOrThrow,
  type ScopeUrl,
} from "./client";
import type { AdminFileEntry, MoveKeysResult, MovedKeyEntry } from "./types/common";

export const adminStorageApi = {
  /** Admin: enriched per-scope listing (format, last_modified,
   * derived products). Same scope check as the user-facing /files
   * endpoint — admins still need scope access. */
  async adminListStorage(
    scope: ScopeUrl,
    opts?: { signal?: AbortSignal },
  ): Promise<AdminFileEntry[]> {
    const r = await authedFetch(
      `${runtime.apiBase()}/admin/scopes/${encodeURIComponent(scope)}/files`,
      { signal: opts?.signal },
    );
    const body = await jsonOrThrow<{ files: AdminFileEntry[] }>(
      r,
      "adminListStorage",
    );
    return body.files;
  },

  /** Admin: delete a source (and all its derived blobs) or a single
   * derived blob. Returns the list of keys actually removed. */
  async adminDeleteBlob(
    scope: ScopeUrl,
    key: string,
  ): Promise<{ deleted: string[]; errors?: string[] }> {
    const r = await authedFetch(
      `${runtime.apiBase()}/admin/scopes/${encodeURIComponent(scope)}/blobs/${encodeURIComponent(key)}`,
      { method: "DELETE" },
    );
    return jsonOrThrow(r, "adminDeleteBlob");
  },

  /** Admin: batch-move source keys into a destination folder
   * (key prefix). Each source is renamed to ``<folder>/<basename>``;
   * derived blobs under ``_derived/<src>.*`` follow so the convert
   * cache is preserved. Returns per-key outcomes. */
  async adminMoveKeysToFolder(
    scope: ScopeUrl,
    keys: string[],
    folder: string,
  ): Promise<MoveKeysResult> {
    const r = await authedFetch(
      `${runtime.apiBase()}/admin/scopes/${encodeURIComponent(scope)}/keys/move-to-folder`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ keys, folder }),
      },
    );
    return jsonOrThrow(r, "adminMoveKeysToFolder");
  },

  /** Admin: rename a single source key in any scope (derived blobs
   * follow). Twin of the user-level renameKey. */
  async adminRenameKey(
    scope: ScopeUrl,
    oldKey: string,
    newKey: string,
  ): Promise<MovedKeyEntry> {
    const r = await authedFetch(
      `${runtime.apiBase()}/admin/scopes/${encodeURIComponent(scope)}/keys/rename`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ old_key: oldKey, new_key: newKey }),
      },
    );
    return jsonOrThrow(r, "adminRenameKey");
  },

  /** Server-side copy keys from another scope into ``dstScope`` (e.g. pulling
   * files from a project/user scope into a corpus). Garage/S3 CopyObject —
   * no download/reupload. Per-key ``{copied, failed}``. */
  async adminCopyKeysFromScope(
    dstScope: ScopeUrl,
    srcScope: ScopeUrl,
    keys: string[],
  ): Promise<{
    copied: Array<{ key: string }>;
    skipped: Array<{ key: string; reason: string }>;
    failed: Array<{ key: string; reason: string }>;
  }> {
    const r = await authedFetch(
      `${runtime.apiBase()}/admin/scopes/${encodeURIComponent(dstScope)}/keys/copy-from`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ src_scope: srcScope, keys }),
      },
    );
    return jsonOrThrow(r, "adminCopyKeysFromScope");
  },

  /** Rename or relocate a folder prefix in place. Walks ``allKeys``
   * for entries under ``oldFolder``, groups them by their parent
   * path *relative to* ``oldFolder``, and issues one
   * ``adminMoveKeysToFolder`` call per group with the corresponding
   * ``<newFolder>/<relative_parent>`` destination. Result aggregates
   * per-call ``moved`` + ``failed`` lists.
   *
   * Why grouped calls instead of one big batch: the move endpoint
   * flattens every input key into a single target folder, so a
   * naïve single call would lose the folder's internal structure
   * (``A/sub/x.ifc`` would land at ``B/x.ifc``, not ``B/sub/x.ifc``).
   * Grouping by relative parent preserves the tree shape.
   */
  async adminRenameOrMoveFolder(
    scope: ScopeUrl,
    oldFolder: string,
    newFolder: string,
    allKeys: string[],
  ): Promise<MoveKeysResult> {
    const groups = groupKeysByRelativeParent(oldFolder, newFolder, allKeys);
    const movedAll: MovedKeyEntry[] = [];
    const failedAll: Array<{ key: string; reason: string }> = [];
    // Sequential not parallel: each call mutates the scope's keyset
    // on the server; concurrent calls would race on collision
    // detection.
    for (const [dest, keys] of groups) {
      const r = await this.adminMoveKeysToFolder(scope, keys, dest);
      movedAll.push(...r.moved);
      failedAll.push(...r.failed);
    }
    return { moved: movedAll, failed: failedAll };
  },
};
