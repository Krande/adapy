// This tab as a viewer an agent can drive (`/api/viewer/...`): register, long-poll for commands, run
// each (./commands.ts) and answer it with the outcome and what the viewer shows now; report that
// state when it changes, so `GET /api/viewer/sessions` is current without a command.
//
// A tab is one session. Every tab of a user registers its own; an agent names one (`session_id`) or
// drives them all. The server drops a session it has not heard from for a while (a sleeping tab),
// and a poll then answers 404: the tab registers again.

import type { ViewerCommandAnswer, ViewerSessionInfo, WireViewerCommand } from "@/services/api/viewerControl";

import { answerPayload, type ViewerCommandPayload } from "./answer";
import { collectViewerState, type ViewerControlDeps } from "./commands";

export interface ViewerControlApi {
  register(client: Record<string, unknown>, state: unknown): Promise<ViewerSessionInfo>;
  poll(sessionId: string, waitS: number, signal?: AbortSignal): Promise<readonly WireViewerCommand[]>;
  answer(sessionId: string, commandId: string, answer: ViewerCommandAnswer): Promise<void>;
  reportState(sessionId: string, state: unknown): Promise<void>;
  close(sessionId: string): void;
}

export interface ViewerControlOptions {
  /** Called with a callback to run whenever what the viewer shows may have changed; returns the
   *  unsubscribe. */
  watch?: (changed: () => void) => () => void;
  /** Describes this tab to the agent listing sessions. */
  client?: Record<string, unknown>;
  /** How long to wait after a failure before trying again, ms. */
  retryMs?: number;
  /** How long a state change waits for more before it is reported, ms. */
  reportDelayMs?: number;
  sleep?: (ms: number, signal: AbortSignal) => Promise<void>;
}

const defaultSleep = (ms: number, signal: AbortSignal) =>
  new Promise<void>((resolve) => {
    const t = setTimeout(resolve, ms);
    signal.addEventListener("abort", () => (clearTimeout(t), resolve()), { once: true });
  });

const errorStatus = (e: unknown): number | null =>
  typeof e === "object" && e !== null && typeof (e as { status?: unknown }).status === "number" ? (e as { status: number }).status : null;

/** Answer one command: run it, then report the outcome and the state it left. */
export async function answerCommand(
  api: ViewerControlApi,
  deps: ViewerControlDeps,
  sessionId: string,
  wire: WireViewerCommand,
): Promise<void> {
  const answer: ViewerCommandAnswer = await answerPayload(deps, wire as unknown as ViewerCommandPayload);
  await api.answer(sessionId, wire.command_id, answer);
}

/** Start serving this tab to the API; returns the function that stops it (and closes the session). */
export function startViewerControl(api: ViewerControlApi, deps: ViewerControlDeps, opts: ViewerControlOptions = {}): () => void {
  const abort = new AbortController();
  const sleep = opts.sleep ?? defaultSleep;
  const retryMs = opts.retryMs ?? 15_000;
  let sessionId: string | null = null;
  let pollS = 25;
  let lastReported = "";
  let reportTimer: ReturnType<typeof setTimeout> | null = null;

  const reportNow = async () => {
    reportTimer = null;
    if (!sessionId || abort.signal.aborted) return;
    let state: unknown;
    try {
      state = collectViewerState(deps);
    } catch {
      return;
    }
    const text = JSON.stringify(state);
    if (text === lastReported) return;
    lastReported = text;
    try {
      await api.reportState(sessionId, state);
    } catch {
      lastReported = ""; // try again on the next change
    }
  };
  const unwatch = opts.watch?.(() => {
    if (reportTimer !== null || abort.signal.aborted) return;
    reportTimer = setTimeout(() => void reportNow(), opts.reportDelayMs ?? 1000);
  });

  const loop = async () => {
    while (!abort.signal.aborted) {
      if (!sessionId) {
        try {
          const state = collectViewerState(deps);
          const info = await api.register(opts.client ?? {}, state);
          sessionId = info.session_id;
          lastReported = JSON.stringify(state);
          pollS = info.poll_s;
        } catch {
          await sleep(retryMs, abort.signal);
          continue;
        }
      }
      let commands: readonly WireViewerCommand[];
      try {
        commands = await api.poll(sessionId, pollS, abort.signal);
      } catch (e) {
        if (abort.signal.aborted) break;
        if (errorStatus(e) === 404) sessionId = null;
        else await sleep(retryMs, abort.signal);
        continue;
      }
      for (const wire of commands) {
        if (abort.signal.aborted) break;
        try {
          await answerCommand(api, deps, sessionId, wire);
        } catch {
          // An answer that could not be posted: the agent sees the command as pending.
        }
      }
    }
  };
  void loop();

  return () => {
    abort.abort();
    unwatch?.();
    if (reportTimer !== null) clearTimeout(reportTimer);
    if (sessionId) api.close(sessionId);
    sessionId = null;
  };
}
