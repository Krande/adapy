import {ApiError} from "@/services/viewerApi";

// "This worker never recorded a manifest" is a NORMAL state, not a failure, and
// the admin UI has two places that must tell it apart from a real one.
//
// A worker captures its package manifest only when it holds a database pool. A
// worker that runs without one -- a supported arrangement, since the pool
// belongs to the API service -- therefore never writes a row, and the endpoint
// answers 404 for the rest of that worker's life. No amount of waiting or
// retrying changes it.
//
// Rendered as a raw error, that is indistinguishable from the admin API being
// broken, and sends people looking for a fault that does not exist. The
// distinction lives here rather than in either component so the two cannot
// drift apart.

/** True when the packages endpoint said "no manifest", rather than failing. */
export function isMissingManifest(e: unknown): boolean {
    return e instanceof ApiError && e.status === 404;
}

/** Shown in place of a package list when none was ever recorded. */
export const MISSING_MANIFEST_NOTE =
    "No package manifest recorded — this worker does not report one.";
