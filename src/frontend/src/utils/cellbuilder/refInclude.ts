/**
 * Whether a plugin's REF (a load/modification/support box, `state/cellbuilder/refsSlice.ts`) is
 * taken by the build -- the ref analogue of `utils/cellbuilder/include.ts`'s `itemIncludeState`
 * for a cell/equipment/opening, and meant to be read the same way: a view that dims or hides what
 * the build leaves out reads this, not `ref.include` alone.
 *
 * `BuilderRef.structure` already names the group a ref belongs to (`"*"` for every structure, or
 * absent for none) -- unlike a cell, a ref never falls back through its `space` to a structure, so
 * this takes `groups` alone, no `cells`.
 */

import {isGroupIncluded, type CellGroup} from "./groups";

/** The ref fields `refIncludeState` reads. */
export type RefInclude = {
  /** Whether the build takes the ref ITSELF (drawn faint, or dropped, when not). */
  include: boolean;
  /** The structure (group) it belongs to, `"*"` for every structure, or absent for none. */
  structure?: string;
};

/** How a ref stands in the build -- the same three states as a cell's `IncludeState`
 * (`utils/cellbuilder/include.ts`):
 *   - `"excluded"`: the ref's own `include` is false;
 *   - `"excluded-by-structure"`: the ref is included itself, but names a structure (not `"*"`)
 *     that is left out of the build (`group.include === false`);
 *   - `"included"`: everything else -- a ref naming `"*"` or no structure is included regardless
 *     of any group's own state, since it belongs to none of them in particular. */
export function refIncludeState(ref: RefInclude, groups: readonly CellGroup[]): "included" | "excluded" | "excluded-by-structure" {
  if (!ref.include) return "excluded";
  const name = ref.structure;
  if (!name || name === "*") return "included";
  const group = groups.find((g) => g.name === name);
  return isGroupIncluded(group) ? "included" : "excluded-by-structure";
}
