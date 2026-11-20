// The hosted viewer's side of `/api/viewer/...` (src/ada/comms/rest/routes/viewer_control.py): a
// single-use ticket for the tab's websocket, and the socket itself. What travels on it, and what the
// tab does with it, is services/viewerControl (socket.ts, wire.ts).

import { runtime } from "@/runtime/config";

import { authedFetch, jsonOrThrow } from "./client";

/** The browser's websocket leg (services/viewerControl/socket.ts): a ticket from the authenticated
 *  `POST /api/viewer/ws-ticket`, then `wss://<this host>/api/viewer/ws?ticket=...`. */
export const browserViewerSocketIO = {
  async ticket(client: Record<string, unknown>): Promise<{ ticket: string; path: string }> {
    return jsonOrThrow<{ ticket: string; path: string }>(
      await authedFetch(`${runtime.apiBase()}/viewer/ws-ticket`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ client }),
      }),
      "viewerControl.ticket",
    );
  },
  url(path: string, ticket: string): string {
    return viewerSocketUrl(window.location.href, runtime.apiBase(), path, ticket);
  },
  open(url: string) {
    return new WebSocket(url);
  },
};

/** The websocket URL for the ticket's `path`: on the API's own origin (`apiBase` may be absolute),
 *  `ws:` for `http:`, `wss:` for `https:`. */
export function viewerSocketUrl(pageUrl: string, apiBase: string, path: string, ticket: string): string {
  const url = new URL(path, new URL(apiBase, pageUrl));
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  url.searchParams.set("ticket", ticket);
  return url.toString();
}