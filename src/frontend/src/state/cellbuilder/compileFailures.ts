/**
 * When a builder COMPILE FAILS: a selector for the failed job, and a listener
 * told once each time a compile ends in an error.
 *
 * For a shell that shows the compile log somewhere of its own (a drawer, say)
 * and opens it on a failure: `selectCompileFailure` for what to show,
 * `onCompileFailed` for when to open it. The log itself is `compileLog`
 * (`CompileLogView` draws it); it arrives a moment after the failure, fetched by
 * the job runner.
 */

import type {CompileJobState} from "./types";
import {useCellBuilderStore} from "./store";

/** A failed compile: the job, with its error. */
export type CompileFailure = CompileJobState & {status: "error"};

/** The last compile when it failed; null while none ran, one runs, or the last one built. */
export function selectCompileFailure(s: {compileJob: CompileJobState | null}): CompileFailure | null {
  const job = s.compileJob;
  return job && job.status === "error" ? (job as CompileFailure) : null;
}

/** Call `listener` each time a compile fails (once per failure, not on later changes of the
 *  store). Returns the unsubscribe. */
export function onCompileFailed(listener: (failure: CompileFailure) => void): () => void {
  return useCellBuilderStore.subscribe((s, prev) => {
    const failure = selectCompileFailure(s);
    if (failure && s.compileJob !== prev.compileJob) listener(failure);
  });
}
