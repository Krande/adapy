// Cross-tab procedural sync over BroadcastChannel (same-origin, zero infra).
//
// The editing tab broadcasts a `preview-ready` message whenever a SERVER build
// (preview or committed compile) of a procedural model lands; a follower tab —
// opened with `?pfollow=<modelId>&pscope=<scope>` — subscribes and loads that
// GLB live. So you can edit the topology on one screen and watch the compiled
// result update on another. Only server builds cross tabs: the follower fetches
// the result by its blob key (both tabs share the same storage + session), so
// an in-browser WASM compile — whose bytes never hit storage — is not broadcast.
//
// BroadcastChannel is same-origin only and needs no server; a browser without it
// (or with the feature blocked) degrades to no cross-tab sync, nothing else.

export interface PreviewReadyMsg {
  type: "preview-ready";
  /** Which procedural model this result belongs to (the follower filters on it). */
  modelId: string;
  /** URL scope part the follower fetches the blob under (e.g. "user:me"). */
  scope: string;
  /** Blob key of the compiled GLB (a `_procedural/…` derived key). */
  derivedKey: string;
  /** Level of detail this build represents. */
  lod: "sim" | "detail";
  /** Human-readable model name, for the follower's banner. */
  name?: string;
}

export type ProceduralMsg = PreviewReadyMsg;

const CHANNEL_NAME = "ada-procedural";

let channel: BroadcastChannel | null = null;

function chan(): BroadcastChannel | null {
  if (typeof BroadcastChannel === "undefined") return null;
  if (!channel) channel = new BroadcastChannel(CHANNEL_NAME);
  return channel;
}

/** Broadcast that a server-built result is ready for a model. No-op where
 * BroadcastChannel is unavailable. */
export function postPreviewReady(msg: Omit<PreviewReadyMsg, "type">): void {
  chan()?.postMessage({ type: "preview-ready", ...msg } satisfies PreviewReadyMsg);
}

/** Subscribe to procedural cross-tab messages. Returns an unsubscribe fn. */
export function subscribeProcedural(
  handler: (msg: ProceduralMsg) => void,
): () => void {
  const c = chan();
  if (!c) return () => {};
  const listener = (e: MessageEvent) => {
    const data = e.data as ProceduralMsg | undefined;
    if (data && typeof data === "object" && data.type === "preview-ready") {
      handler(data);
    }
  };
  c.addEventListener("message", listener);
  return () => c.removeEventListener("message", listener);
}

/** Parse the follower params from a URL (`?pfollow=<modelId>&pscope=<scope>`).
 * Returns null when this tab is not a procedural follower. */
export function followerParams(
  search: string = typeof window !== "undefined" ? window.location.search : "",
): { modelId: string; scope: string } | null {
  const params = new URLSearchParams(search);
  const modelId = params.get("pfollow");
  if (!modelId) return null;
  return { modelId, scope: params.get("pscope") || "user:me" };
}

/** Build the URL that opens a follower window for a model in the current tab's
 * origin/path, carrying the model + scope as query params. */
export function followerUrl(modelId: string, scope: string): string {
  const url = new URL(window.location.href);
  url.searchParams.set("pfollow", modelId);
  url.searchParams.set("pscope", scope);
  // A follower only shows results — drop any hash that would re-open editor UI.
  url.hash = "";
  return url.toString();
}
