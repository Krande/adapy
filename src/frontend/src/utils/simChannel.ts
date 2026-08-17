// Cross-tab / cross-window simulation sync over BroadcastChannel (same-origin,
// zero infra) — the Simulation-panel twin of `proceduralChannel.ts`.
//
// A maximized / new-window Simulation view (opened with `?simfollow=<source>&
// panel=<pluginId>`) mounts the plugin's tab full-window and drives the original
// docked tab's 3D scene: it echoes the user's actions (row selection, active
// case, active field, run-option changes, run triggers) back over this channel,
// and the viewer tab applies them to its stores so the scene repaints. The
// messages are intentionally bidirectional so either side can be the source —
// the docked tab also broadcasts its own selection changes so a late-joining
// follower can catch up via the `hello` handshake.
//
// BroadcastChannel is same-origin only and needs no server; a browser without it
// degrades to no cross-window sync, nothing else.

/** A result row was selected (3D pick or list click). */
export interface SimSelectMsg {
  t: "select";
  modelId: string | null;
  rowKey: string | null;
}

/** The active run + result case changed. */
export interface SimCaseMsg {
  t: "case";
  runId: string | null;
  caseId: string | null;
}

/** The active colour field (check metric) changed; `failedOnly` filters rows. */
export interface SimFieldMsg {
  t: "field";
  fieldId: string;
  failedOnly?: boolean;
}

/** A run-option changed (check type / group / assumptions / worst-case subset …).
 *  Opaque to core — the plugin owns the option shape. */
export interface SimRunOptsMsg {
  t: "runopts";
  opts: Record<string, unknown>;
}

/** Trigger a check/run with the given options (the follower enqueues the job). */
export interface SimRunMsg {
  t: "run";
  options: Record<string, unknown>;
}

/** Handshake: a viewer or follower announcing itself so the other side can
 *  re-broadcast its current state (late-follower catch-up). */
export interface SimHelloMsg {
  t: "hello";
  role: "viewer" | "follower";
}

export type SimMsg =
  | SimSelectMsg
  | SimCaseMsg
  | SimFieldMsg
  | SimRunOptsMsg
  | SimRunMsg
  | SimHelloMsg;

const CHANNEL_NAME = "ada-sim";

let channel: BroadcastChannel | null = null;

function chan(): BroadcastChannel | null {
  if (typeof BroadcastChannel === "undefined") return null;
  if (!channel) channel = new BroadcastChannel(CHANNEL_NAME);
  return channel;
}

const SIM_TAGS: ReadonlySet<SimMsg["t"]> = new Set([
  "select",
  "case",
  "field",
  "runopts",
  "run",
  "hello",
]);

/** Broadcast a simulation message. No-op where BroadcastChannel is unavailable. */
export function postSim(msg: SimMsg): void {
  chan()?.postMessage(msg);
}

/** Subscribe to simulation messages. Returns an unsubscribe fn. */
export function subscribeSim(handler: (msg: SimMsg) => void): () => void {
  const c = chan();
  if (!c) return () => {};
  const listener = (e: MessageEvent) => {
    const data = e.data as SimMsg | undefined;
    if (data && typeof data === "object" && SIM_TAGS.has((data as SimMsg).t)) {
      handler(data);
    }
  };
  c.addEventListener("message", listener);
  return () => c.removeEventListener("message", listener);
}

/** Params identifying a Simulation follower window
 *  (`?simfollow=<source>&panel=<pluginId>`). `source` is the scope-qualified
 *  model/source key the follower drives (and enqueues jobs against); `panel` is
 *  the plugin tab id to mount full-window. Returns null for a normal tab. */
export function followerParams(
  search: string = typeof window !== "undefined" ? window.location.search : "",
): { source: string; panel: string; scope: string } | null {
  const params = new URLSearchParams(search);
  const source = params.get("simfollow");
  if (!source) return null;
  return {
    source,
    panel: params.get("panel") || "",
    scope: params.get("scope") || "user:me",
  };
}

/** Build the URL that opens a Simulation follower window for a source + plugin
 *  tab in the current tab's origin/path. */
export function followerUrl(source: string, panel: string, scope?: string): string {
  const url = new URL(window.location.href);
  url.searchParams.set("simfollow", source);
  if (panel) url.searchParams.set("panel", panel);
  if (scope) url.searchParams.set("scope", scope);
  // A follower is results/controls only — drop any hash that would re-open other
  // viewer UI.
  url.hash = "";
  return url.toString();
}
