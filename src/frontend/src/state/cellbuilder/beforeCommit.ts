/**
 * Cellbuilder BEFORE-COMMIT hooks.
 *
 * Owns: the checks a shell or a plugin runs before the open procedural model is
 * committed, each able to veto it. Every commit path goes through the store's
 * `commit` (the toolbar's Commit, Ctrl+S, the commit before a compile or an
 * export), so a hook here sees them all. Engine-agnostic: a hook is handed the doc
 * about to be committed and the doc's extras (the lists the builder does not own),
 * and answers whether the commit may go on.
 *
 * A hook that answers `false` stops the commit (the store's `commit` returns false
 * and leaves the model as it is, dirty); `true` or nothing lets it go on. A hook
 * that throws is logged and does not stop it: a broken check must not lock a user
 * out of saving. Hooks run one after the other, in registration order.
 */

import type {ProceduralDoc} from "@/services/viewerApi";

/** What a hook is handed: the doc as it will be committed, and its extras. */
export interface BeforeCommitContext {
  doc: ProceduralDoc;
  docExtras: Readonly<Record<string, unknown>>;
}

/** A check before a commit: false vetoes it. */
export type BeforeCommitHook = (ctx: BeforeCommitContext) => boolean | void | Promise<boolean | void>;

const hooks = new Map<string, BeforeCommitHook>();

/** Run `hook` before every commit, under `owner` (registering an owner again replaces its hook).
 *  Returns the unregister. */
export function registerBeforeCommit(owner: string, hook: BeforeCommitHook): () => void {
  hooks.set(owner, hook);
  return () => {
    if (hooks.get(owner) === hook) hooks.delete(owner);
  };
}

/** Run every hook; false when one vetoed the commit. */
export async function runBeforeCommit(ctx: BeforeCommitContext): Promise<boolean> {
  for (const [owner, hook] of [...hooks]) {
    try {
      if ((await hook(ctx)) === false) return false;
    } catch (err) {
      console.warn(`[builder] before-commit hook of "${owner}" threw -- the commit goes on`, err);
    }
  }
  return true;
}
