// The retired layout-engine flag.
//
// The UI used to have two paths: the hand-written tree and the same UI rendered
// from the panel registry by the layout host. The hand-written tree is gone, so
// the layout host is the only path and there is nothing left to switch:
// `?layout=classic` and `localStorage["ada:layout-engine"]` are ignored (the
// classic look is the classic preset, which every path renders).
//
// Kept because `@/viewer-core/layout` exported these in 1.12.0.

/** @deprecated The layout engine is the only path; the URL parameter is ignored. */
export const LAYOUT_ENGINE_PARAM = "layout";
/** @deprecated The layout engine is the only path; the stored flag is ignored. */
export const LAYOUT_ENGINE_STORAGE_KEY = "ada:layout-engine";

/** The part of `Storage` the flag used to read. */
export interface FlagStorage {
  getItem(key: string): string | null;
}

/**
 * Always true: the layout engine is the only path the UI renders through.
 *
 * @deprecated Nothing to ask any more. The arguments are accepted and ignored.
 */
export function layoutEngineEnabled(_search?: string, _storage?: FlagStorage | null): boolean {
  return true;
}
