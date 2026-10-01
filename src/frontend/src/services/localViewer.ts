// Stopping the LOCAL viewer this page is served by.
//
// A viewer started on a workstation keeps running after its tab closes. When
// its launcher enabled it, the server has `POST /api/local/shutdown`, and this
// is the one call a UI makes to use it. The server side, and why a deployment
// can never have the route, is `ada/comms/rest/local_shutdown.py`.
//
// Two steps, because the token must not be readable by another site:
//   1. `window.ADA_LOCAL_SHUTDOWN` (from /config.js) says the route exists, so a
//      UI can decide to show a control without a request;
//   2. the token comes from `GET /api/config` (`localShutdown.token`), JSON
//      read by fetch and so same-origin only, and is sent in a header.
//
// Stopping ends the viewer for every tab open on it. A UI should confirm first,
// and afterwards show that the viewer has stopped rather than let the requests
// that follow fail one by one.

import { runtime } from "@/runtime/config";

export type StopLocalViewerResult = { ok: true } | { ok: false; error: string };

type FetchLike = (input: string, init?: RequestInit) => Promise<Response>;

interface LocalShutdownEntry {
  available?: unknown;
  token?: unknown;
  header?: unknown;
}

const DEFAULT_HEADER = "X-Ada-Local-Shutdown";

/** Whether this page is on a local viewer it may stop. */
export function localShutdownAvailable(): boolean {
  return runtime.localShutdownAvailable();
}

async function detail(response: Response): Promise<string> {
  try {
    const body = await response.json();
    if (body && typeof body.detail === "string") return body.detail;
  } catch {
    // Not JSON: the status says enough.
  }
  return `HTTP ${response.status}`;
}

/** Ask the local viewer to stop. Resolves once it has accepted; it exits a
 *  moment later. Never throws. */
export async function stopLocalViewer(
  fetchImpl: FetchLike = (input, init) => fetch(input, init),
  apiBase: string = runtime.apiBase(),
): Promise<StopLocalViewerResult> {
  try {
    const config = await fetchImpl(`${apiBase}/config`, { cache: "no-store" });
    if (!config.ok) return { ok: false, error: `could not read the viewer's config: ${await detail(config)}` };
    const entry = ((await config.json()) as { localShutdown?: LocalShutdownEntry })?.localShutdown;
    if (!entry || entry.available !== true || typeof entry.token !== "string" || !entry.token) {
      return { ok: false, error: "this viewer cannot be stopped from the page" };
    }
    const header = typeof entry.header === "string" && entry.header ? entry.header : DEFAULT_HEADER;
    const reply = await fetchImpl(`${apiBase}/local/shutdown`, {
      method: "POST",
      headers: { [header]: entry.token },
    });
    if (!reply.ok) return { ok: false, error: await detail(reply) };
    return { ok: true };
  } catch (err) {
    return { ok: false, error: err instanceof Error ? err.message : String(err) };
  }
}
