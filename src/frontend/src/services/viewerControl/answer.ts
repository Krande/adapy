// What a tab answers to what an agent sent it -- one command, or a batch run in order -- whichever
// transport brought it: the outcome, a batch's per-command steps, and what the viewer shows now.

import {
  collectViewerState,
  runViewerBatch,
  runViewerCommand,
  type ViewerBatchStep,
  type ViewerCommand,
  type ViewerControlDeps,
} from "./commands";

/** What the server sends: `{command}` or `{commands}` (`viewer_control.CommandRequest.payload`). */
export interface ViewerCommandPayload {
  readonly command?: ViewerCommand | null;
  readonly commands?: readonly ViewerCommand[] | null;
}

export interface ViewerAnswer {
  readonly ok: boolean;
  readonly error: string | null;
  readonly result: unknown;
  readonly steps?: ViewerBatchStep[];
  readonly state: unknown;
}

function stateOf(deps: ViewerControlDeps): unknown {
  try {
    return collectViewerState(deps);
  } catch {
    return null; // the state could not be read; the outcome is still the answer
  }
}

export async function answerPayload(deps: ViewerControlDeps, payload: ViewerCommandPayload): Promise<ViewerAnswer> {
  if (Array.isArray(payload.commands)) {
    const { steps, error } = await runViewerBatch(deps, payload.commands);
    return { ok: error === null, error, result: null, steps, state: stateOf(deps) };
  }
  if (!payload.command) return { ok: false, error: "no command", result: null, state: stateOf(deps) };
  try {
    const result = await runViewerCommand(deps, payload.command);
    return { ok: true, error: null, result: result ?? null, state: stateOf(deps) };
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : String(e), result: null, state: stateOf(deps) };
  }
}
