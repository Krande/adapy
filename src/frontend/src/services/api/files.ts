// User-scoped blob storage: list/rename/move/upload/download source and
// derived files. Shared with services/api/adminStorage.ts via the same
// folder-move grouping helper and file-entry DTOs.

import { runtime } from "@/runtime/config";

import {
  getAccessToken,
  refreshAccessToken,
} from "@/services/auth/oidc";
import {
  ApiError,
  authedFetch,
  groupKeysByRelativeParent,
  jsonOrThrow,
  readDetail,
  type ScopeUrl,
  type TargetFormat,
} from "./client";
import type {
  AdminFileEntry,
  FileEntry,
  MoveKeysResult,
  MovedKeyEntry,
} from "./types/common";

export const filesApi = {
  /** Direct URL for the addressable blob endpoint. Includes scope.
   * Only safe to use as `<a href download>` when auth is disabled —
   * with auth on use :func:`downloadBlob` so the bearer token rides
   * along. */
  blobUrl(scope: ScopeUrl, key: string): string {
    return `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/blobs/${encodeURIComponent(key)}`;
  },

  async listFiles(scope: ScopeUrl): Promise<FileEntry[]> {
    const r = await authedFetch(
      `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/files`,
    );
    const body = await jsonOrThrow<{ files: FileEntry[] }>(
      r,
      `listFiles(${scope})`,
    );
    return body.files;
  },

  /** Saved utility overlays (_overlays/<model>.<utility>.glb) for the scope. The utils
   * menu filters these by the loaded model so an overlay generated on one model only
   * shows when that model is loaded. */
  async listOverlays(
    scope: ScopeUrl,
  ): Promise<{ key: string; size: number; last_modified: string | null }[]> {
    const r = await authedFetch(
      `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/overlays`,
    );
    const body = await jsonOrThrow<{
      overlays: { key: string; size: number; last_modified: string | null }[];
    }>(r, `listOverlays(${scope})`);
    return body.overlays;
  },

  /** Same scope file listing as ``listFiles`` but with each source's
   * existing derived blobs grouped under it. The /convert page uses
   * this to show pre-existing conversions next to fresh upload rows
   * — the user wants to spot "I already converted this last week,
   * just give me the GLB" without re-running the converter. Server
   * filters orphan derived (no matching source in this scope); use
   * the admin storage list for cleanup. */
  async listFilesWithDerived(scope: ScopeUrl): Promise<AdminFileEntry[]> {
    const r = await authedFetch(
      `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/files?include_derived=1`,
    );
    const body = await jsonOrThrow<{ files: AdminFileEntry[] }>(
      r,
      `listFilesWithDerived(${scope})`,
    );
    return body.files;
  },

  /** Trigger a browser download of a stored blob. Fetches with auth,
   * materialises a blob: URL, clicks a hidden anchor, then revokes
   * the URL to release memory. Works in both auth-on and auth-off
   * modes — the only cost over `<a href>` is one extra round-trip
   * the browser would have made anyway. */
  async downloadBlob(
    scope: ScopeUrl,
    key: string,
    suggestedName: string,
  ): Promise<void> {
    const r = await authedFetch(this.blobUrl(scope, key));
    if (!r.ok) {
      throw new ApiError(`downloadBlob(${key})`, r.status, await readDetail(r));
    }
    const blob = await r.blob();
    const url = URL.createObjectURL(blob);
    try {
      const a = document.createElement("a");
      a.href = url;
      a.download = suggestedName;
      a.style.display = "none";
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
    } finally {
      URL.revokeObjectURL(url);
    }
  },

  /** Delete an own file (derived blobs cascade server-side).
   * Personal scope only — shared/project scopes return 403; admins
   * use adminDeleteBlob there. */
  async deleteBlob(
    scope: ScopeUrl,
    key: string,
  ): Promise<{ deleted: string[]; errors?: string[] }> {
    const r = await authedFetch(this.blobUrl(scope, key), { method: "DELETE" });
    return jsonOrThrow(r, "deleteBlob");
  },

  /** Batch-move own source keys into a destination folder. Personal
   * scope only; mirrors adminMoveKeysToFolder. */
  async moveKeysToFolder(
    scope: ScopeUrl,
    keys: string[],
    folder: string,
  ): Promise<MoveKeysResult> {
    const r = await authedFetch(
      `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/keys/move-to-folder`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ keys, folder }),
      },
    );
    return jsonOrThrow(r, "moveKeysToFolder");
  },

  /** Rename a single own source key (derived blobs follow). Personal
   * scope only. 409 → target exists, 404 → source missing. */
  async renameKey(
    scope: ScopeUrl,
    oldKey: string,
    newKey: string,
  ): Promise<MovedKeyEntry> {
    const r = await authedFetch(
      `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/keys/rename`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ old_key: oldKey, new_key: newKey }),
      },
    );
    return jsonOrThrow(r, "renameKey");
  },

  /** Rename or relocate a folder prefix in the personal scope —
   * user-level twin of adminRenameOrMoveFolder (same grouped-move
   * strategy, see that method's docstring). */
  async renameOrMoveFolder(
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
      const r = await this.moveKeysToFolder(scope, keys, dest);
      movedAll.push(...r.moved);
      failedAll.push(...r.failed);
    }
    return { moved: movedAll, failed: failedAll };
  },

  /** Fetch raw bytes for a key. Used by the in-browser Pyodide
   * pipeline to read its source from storage. */
  async getBlob(scope: ScopeUrl, key: string): Promise<ArrayBuffer> {
    const r = await authedFetch(this.blobUrl(scope, key));
    if (!r.ok) {
      throw new ApiError(`getBlob(${key})`, r.status, await readDetail(r));
    }
    return await r.arrayBuffer();
  },

  /** Fetch a byte range `[start, end]` (inclusive) of a stored object.
   * Returns the bytes and whether the server honoured the range (206)
   * or ignored it and sent the whole object (200 — e.g. a gzip-at-rest
   * blob that can't be ranged). The FEA viewer uses this to pull a
   * single field step instead of the whole multi-step blob. */
  async getBlobRange(
    scope: ScopeUrl,
    key: string,
    start: number,
    end: number,
  ): Promise<{ buf: ArrayBuffer; ranged: boolean }> {
    // Send the range BOTH as ?range_start/range_end query params and as a
    // Range header. The query params are proxy-proof — some ingresses/CDNs
    // (seen on the mobile path) strip the Range header, which would
    // silently return the whole multi-step blob. The header stays for any
    // cache/proxy that prefers it; the server replies 206 either way.
    const base = this.blobUrl(scope, key);
    const url = `${base}${base.includes("?") ? "&" : "?"}range_start=${start}&range_end=${end}`;
    const r = await authedFetch(url, {
      headers: { Range: `bytes=${start}-${end}` },
    });
    if (!r.ok && r.status !== 206) {
      throw new ApiError(`getBlobRange(${key})`, r.status, await readDetail(r));
    }
    const buf = await r.arrayBuffer();
    // 206 ⇒ honoured (header or query-param path); 200 ⇒ a proxy/old
    // backend served the whole object → caller parses + slices.
    return { buf, ranged: r.status === 206 };
  },

  /** Upload bytes under a given key. body is anything fetch/XHR can
   * send (File, Blob, ArrayBuffer, ...). When `onProgress` is given,
   * the request goes through XMLHttpRequest because fetch doesn't
   * expose upload progress consistently across browsers. */
  async putBlob(
    scope: ScopeUrl,
    key: string,
    body: BodyInit,
    opts?: { onProgress?: (loaded: number, total: number) => void },
  ): Promise<void> {
    if (!opts?.onProgress) {
      const r = await authedFetch(this.blobUrl(scope, key), {
        method: "PUT",
        body,
        headers: { "Content-Type": "application/octet-stream" },
      });
      if (!r.ok) {
        throw new ApiError(`putBlob(${key})`, r.status, await readDetail(r));
      }
      return;
    }

    // Progress-tracked path uses XHR. authedFetch's refresh-then-
    // retry pattern is open-coded here so the upload survives a
    // token expiring just before the PUT lands — observed when a
    // user picks a large file after a long idle.
    const fireUpload = (): Promise<void> =>
      new Promise<void>((resolve, reject) => {
        const xhr = new XMLHttpRequest();
        xhr.open("PUT", this.blobUrl(scope, key));
        xhr.setRequestHeader("Content-Type", "application/octet-stream");
        const t = getAccessToken();
        if (t) xhr.setRequestHeader("Authorization", `Bearer ${t}`);
        xhr.upload.addEventListener("progress", (e) => {
          if (e.lengthComputable) {
            opts.onProgress!(e.loaded, e.total);
          }
        });
        xhr.addEventListener("load", () => {
          if (xhr.status >= 200 && xhr.status < 300) {
            resolve();
          } else {
            reject(
              new ApiError(
                `putBlob(${key}) failed: ${xhr.status}`,
                xhr.status,
                xhr.responseText || "",
              ),
            );
          }
        });
        xhr.addEventListener("error", () =>
          reject(new ApiError(`putBlob(${key}) network error`, 0, "")),
        );
        xhr.addEventListener("abort", () =>
          reject(new ApiError(`putBlob(${key}) aborted`, 0, "")),
        );
        xhr.send(body as XMLHttpRequestBodyInit);
      });

    // Pre-flight: if our cached token has fallen out of the 30s
    // skew window, refresh before we start the (potentially slow)
    // upload so the body isn't sent with no Authorization header.
    if (!getAccessToken()) {
      await refreshAccessToken();
    }
    try {
      await fireUpload();
    } catch (e) {
      if (!(e instanceof ApiError) || e.status !== 401) throw e;
      const refreshed = await refreshAccessToken();
      if (!refreshed) throw e;
      await fireUpload();
    }
  },

  /** Upload a pyodide-derived blob (e.g. an in-browser GLB conversion
   * of a STEP/IFC source) and return the canonical derived key the
   * server stored it under. Wraps PUT /api/scopes/{scope}/derived,
   * which computes the key from (source, target) so the SPA doesn't
   * need to mirror the server's naming convention. */
  async putDerivedBlob(
    scope: ScopeUrl,
    sourceKey: string,
    target: TargetFormat,
    body: BodyInit,
  ): Promise<string> {
    // managed_audit=1: the WASM pipeline records its own metrics-rich
    // audit row via auditLocalCreate/Update, so tell the derived-PUT
    // not to also auto-audit (which would double-count the conversion).
    const url =
      `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/derived` +
      `?source=${encodeURIComponent(sourceKey)}&target=${encodeURIComponent(target)}&managed_audit=1`;
    const r = await authedFetch(url, {
      method: "PUT",
      body,
      headers: { "Content-Type": "application/octet-stream" },
    });
    if (!r.ok) {
      throw new ApiError(
        `putDerivedBlob(${sourceKey})`,
        r.status,
        await readDetail(r),
      );
    }
    const j: { key: string; size: number } = await r.json();
    return j.key;
  },

  /** Request a presigned PUT URL for a too-large-to-buffer upload.
   *
   * Used by uploadFile when the file exceeds the server's regular
   * upload cap (~200 MB). Server returns a one-shot URL the browser
   * PUTs the raw bytes to directly. Local-backed deployments 503
   * here — operator must run with an S3-compatible backend. */
  async requestUploadUrl(
    scope: ScopeUrl,
    key: string,
    /** The file's byte size, if known. Not verified against anything server-
     * side — it never gates a decision, only seeds the "uploading" row a
     * second tab or a page reload would otherwise show with no size at all
     * until the first upload-progress heartbeat arrives. Always known here:
     * every caller has a `File`, whose `.size` is free to read. */
    size?: number,
  ): Promise<{
    url: string;
    key: string;
    method: string;
    expires_in_seconds: number;
    /** Server hint: when set, the client should compress the body
     * with this encoding and send Content-Encoding: <value> on the
     * PUT. The encoding header is *not* signed into the URL — sent
     * as opaque metadata — so a client lacking CompressionStream
     * can ignore it and PUT raw bytes; the sweep job will pick it
     * up later. */
    content_encoding?: string | null;
  }> {
    const r = await authedFetch(
      `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/upload-url`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(
          typeof size === "number" ? { key, size } : { key },
        ),
      },
    );
    return jsonOrThrow(r, `requestUploadUrl(${key})`);
  },

  /** Heartbeat the browser's own upload-progress event to the server, so a
   * SECOND tab, a second user, or the same tab after a reload can see real
   * progress via `GET /files` instead of a bare "uploading" with no number —
   * the API cannot observe a direct browser→object-store PUT any other way.
   * Best-effort: swallow failures rather than surface them, since a missed
   * heartbeat is a slightly stale progress bar, never a broken upload — the
   * PUT itself does not go through this call at all. */
  async uploadProgress(
    scope: ScopeUrl,
    key: string,
    loaded: number,
    total: number,
  ): Promise<void> {
    try {
      await authedFetch(
        `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/upload-progress`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ key, loaded, total }),
        },
      );
    } catch {
      // Best-effort — see the docstring above.
    }
  },

  /** Request a presigned GET URL for direct, Range-capable download from
   * the object store. Mirrors requestUploadUrl. Used by the in-browser
   * streaming converter to read a huge source (e.g. a multi-GB SIN) in
   * ranges without API-tunneling the whole transfer. Local-backed
   * deployments 503 here — callers fall back to the buffered getBlob path. */
  async requestDownloadUrl(
    scope: ScopeUrl,
    key: string,
  ): Promise<{
    url: string;
    key: string;
    method: string;
    expires_in_seconds: number;
    size: number;
  }> {
    const r = await authedFetch(
      `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/download-url`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ key }),
      },
    );
    return jsonOrThrow(r, `requestDownloadUrl(${key})`);
  },

  /** Finalise a presigned-URL upload: server confirms the object
   * landed and writes the audit row. Caller should run this only
   * after a successful direct PUT — otherwise it 404s. */
  async completeUpload(
    scope: ScopeUrl,
    key: string,
  ): Promise<{ key: string; size: number }> {
    const r = await authedFetch(
      `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/upload-complete`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ key }),
      },
    );
    return jsonOrThrow(r, `completeUpload(${key})`);
  },
};
