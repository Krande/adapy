// `POST /api/scopes/{scope}/export-selection` -- a selected element of a loaded model, and
// everything under it, written as a STEP or IFC file (`routes/export_selection.py`).
//
// An ordinary worker job, polled through the SAME `/convert/{job_id}` route every other job in
// this viewer uses; the file lands at `derived_key` and is fetched through the blob route. This
// module returns WIRE shapes only; `@/utils/export/selectionExport` is what decides whether a
// selection can be exported and drives the request.

import { runtime } from "@/runtime/config";

import { authedFetch, jsonOrThrow, type ScopeUrl } from "./client";

export interface SelectionExportResponse {
  readonly job_id: string | null;
  readonly derived_key: string;
  /** `true`: the file already sits at `derived_key` -- nothing was enqueued. */
  readonly cached: boolean;
  /** What the download should be called, as the route named it. */
  readonly filename: string;
}

export const selectionExportApi = {
  async exportSelection(scope: ScopeUrl, body: Record<string, unknown>): Promise<SelectionExportResponse> {
    const r = await authedFetch(`${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/export-selection`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    return jsonOrThrow<SelectionExportResponse>(r, "exportSelection");
  },
};
