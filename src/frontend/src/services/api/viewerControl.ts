// The viewer's side of `/api/viewer/...` (src/ada/comms/rest/routes/viewer_control.py): this tab
// registers a session, long-polls for the commands an agent sends it, answers each with its
// outcome and what it shows now, and reports that state when it changes. The commands themselves
// are run by `services/viewerControl`.

import { runtime } from "@/runtime/config";

import { authedFetch, jsonOrThrow } from "./client";

/** One command as the server sends it (validated there: `viewer_control.ViewerCommand`). */
export interface WireViewerCommand {
  readonly command_id: string;
  readonly command: { readonly action: string } & Readonly<Record<string, unknown>>;
  readonly issued_at: number;
}

/** A tab's answer to one command. */
export interface ViewerCommandAnswer {
  readonly ok: boolean;
  readonly error?: string | null;
  readonly result?: unknown;
  readonly state?: unknown;
}

export interface ViewerSessionInfo {
  readonly session_id: string;
  /** How long the server may hold a poll, seconds. */
  readonly poll_s: number;
}

const base = (): string => `${runtime.apiBase()}/viewer/sessions`;

export const viewerControlApi = {
  async register(client: Record<string, unknown>, state: unknown): Promise<ViewerSessionInfo> {
    return jsonOrThrow<ViewerSessionInfo>(
      await authedFetch(base(), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ client, state }),
      }),
      "viewerControl.register",
    );
  },

  /** The session's commands; resolves within `waitS` with none when nothing came. A 404 (the
   *  session expired or was replaced) throws an `ApiError` with `status` 404. */
  async poll(sessionId: string, waitS: number, signal?: AbortSignal): Promise<readonly WireViewerCommand[]> {
    const r = await authedFetch(`${base()}/${encodeURIComponent(sessionId)}/commands?wait_s=${waitS}`, { signal });
    return (await jsonOrThrow<{ commands?: WireViewerCommand[] }>(r, "viewerControl.poll")).commands ?? [];
  },

  async answer(sessionId: string, commandId: string, answer: ViewerCommandAnswer): Promise<void> {
    await jsonOrThrow(
      await authedFetch(`${base()}/${encodeURIComponent(sessionId)}/commands/${encodeURIComponent(commandId)}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(answer),
      }),
      "viewerControl.answer",
    );
  },

  async reportState(sessionId: string, state: unknown): Promise<void> {
    await jsonOrThrow(
      await authedFetch(`${base()}/${encodeURIComponent(sessionId)}/state`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ state }),
      }),
      "viewerControl.reportState",
    );
  },

  /** Best effort, as the tab closes: `keepalive` lets the request outlive the page. */
  close(sessionId: string): void {
    void authedFetch(`${base()}/${encodeURIComponent(sessionId)}`, { method: "DELETE", keepalive: true }).catch(() => {});
  },
};
