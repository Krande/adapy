// Robot-asset bundles (`.adabots`): the provider store's robot assets listed, exported and imported
// (`ada/comms/rest/routes/admin_robot_assets.py`). Admin only. The shapes are in `@/assets/robotBundles`.

import { runtime } from "@/runtime/config";
import {
  filenameFromDisposition,
  ROBOT_BUNDLE_SUFFIX,
  type RobotAssetList,
  type RobotImportChoices,
  type RobotImportPreview,
  type RobotImportResult,
} from "@/assets/robotBundles";
import { getAccessToken, isAuthEnabled, refreshAccessToken } from "@/services/auth/oidc";

import { ApiError, authHeader, authedFetch, detailText, jsonOrThrow } from "./client";

const base = () => `${runtime.apiBase()}/admin/robot-assets`;

/** What to export: refs (`id@version`, or `id` for every version of it), or everything. */
export type RobotBundleSelection = { refs: readonly string[] } | { all: true };

export interface RobotBundleDownload {
  blob: Blob;
  filename: string;
}

export const robotBundlesApi = {
  /** The stored robots, each with its versions (newest first), the scopes that read them, the limits. */
  async list(): Promise<RobotAssetList> {
    return jsonOrThrow<RobotAssetList>(await authedFetch(base()), "robotBundles.list");
  },

  /** The bundle of a selection. The server streams it; the browser keeps it as a Blob to save. */
  async exportBundle(selection: RobotBundleSelection, signal?: AbortSignal): Promise<RobotBundleDownload> {
    const body = "all" in selection ? { all: true } : { refs: [...selection.refs] };
    const r = await authedFetch(`${base()}/export`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal,
    });
    if (!r.ok) await jsonOrThrow(r, "robotBundles.export");
    const filename = filenameFromDisposition(r.headers.get("content-disposition"), `robots${ROBOT_BUNDLE_SUFFIX}`);
    return { blob: await r.blob(), filename };
  },

  /** Upload a bundle for its preview: validated and staged, nothing written to the assets.
   *  `onProgress` is handed the share of the upload sent (0..1). A refused bundle rejects with an
   *  ApiError whose `detail` carries the problems (`refusalProblems`). */
  async previewImport(file: Blob, onProgress?: (sent: number) => void, signal?: AbortSignal): Promise<RobotImportPreview> {
    if (isAuthEnabled() && !getAccessToken()) await refreshAccessToken();
    return new Promise<RobotImportPreview>((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open("POST", `${base()}/import/preview`);
      xhr.setRequestHeader("Content-Type", "application/octet-stream");
      for (const [k, v] of Object.entries(authHeader())) xhr.setRequestHeader(k, v);
      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable && onProgress) onProgress(e.total ? e.loaded / e.total : 0);
      };
      xhr.onload = () => {
        if (xhr.status >= 200 && xhr.status < 300) {
          try {
            resolve(JSON.parse(xhr.responseText) as RobotImportPreview);
          } catch (e) {
            reject(e);
          }
          return;
        }
        const reason = detailText(xhr.responseText);
        reject(
          new ApiError(
            `robotBundles.preview failed: ${xhr.status}${reason ? ` — ${reason}` : ""}`,
            xhr.status,
            xhr.responseText,
          ),
        );
      };
      xhr.onerror = () => reject(new ApiError("robotBundles.preview failed: the upload did not get through", 0));
      xhr.onabort = () => reject(new DOMException("aborted", "AbortError"));
      signal?.addEventListener("abort", () => xhr.abort());
      xhr.send(file);
    });
  },

  /** Import a previewed bundle with the choices made in the preview. */
  async applyImport(uploadId: string, choices: RobotImportChoices = {}): Promise<RobotImportResult> {
    const r = await authedFetch(`${base()}/import/${encodeURIComponent(uploadId)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(choices),
    });
    return jsonOrThrow<RobotImportResult>(r, "robotBundles.import");
  },

  /** Drop a previewed bundle without importing it. */
  async discardImport(uploadId: string): Promise<void> {
    await authedFetch(`${base()}/import/${encodeURIComponent(uploadId)}`, { method: "DELETE" });
  },
};

/** Hand a downloaded bundle to the browser to save. */
export function saveBundle({ blob, filename }: RobotBundleDownload): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.rel = "noopener";
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 30_000);
}
