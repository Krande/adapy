// What each loaded model's ROOT row in the Scene tree is called.
//
// Every root carries two names (set when the model is loaded, `cacheAndBuildTree`):
//
//   top_name      what the model calls its top -- the name the loader was handed (a site's label
//                 in Sources, a model's name in External models), else the model's own root node
//                 when it has a real one, else the source label;
//   source_label  the unique id it was loaded under, as the tree always showed it (a file name,
//                 or the tail of a provider's source key -- unique, and meaningless to read).
//
// Which one shows is the viewer's choice (`RootLabelMode`), remembered per browser. Either way
// labels stay distinct (-2, -3 ...), since two loads of the same site from two providers are two
// rows that must be told apart.

import type { TreeNodeData } from "@/components/tree_view/CustomNode";

export type RootLabelMode = "name" | "id";

const STORAGE_KEY = "ada.sceneTree.rootLabels";

/** Root names that say nothing: what an exporter calls a scene it did not name. */
const GENERIC_ROOT_NAMES = new Set(["", "root", "scene", "auxscene", "model", "world", "node", "group"]);

/** The name a model's root row shows in "name" mode. */
export function topLevelName(displayName: string | null | undefined, rootName: string | null | undefined, sourceLabel: string): string {
  const given = displayName?.trim();
  if (given) return given;
  const own = rootName?.trim() ?? "";
  if (own && !GENERIC_ROOT_NAMES.has(own.toLowerCase())) return own;
  return sourceLabel;
}

/** `base`, or `base-2`, `base-3` ... -- the first not already in `taken`. */
export function distinctLabel(base: string, taken: ReadonlySet<string>): string {
  if (!taken.has(base)) return base;
  let n = 2;
  while (taken.has(`${base}-${n}`)) n++;
  return `${base}-${n}`;
}

/** The container with every root renamed for `mode`, in order, kept distinct. Root rows are copied
 *  (a new object per root, so the tree redraws them); everything below them is shared as is. */
export function labelRoots(container: TreeNodeData, mode: RootLabelMode): TreeNodeData {
  const taken = new Set<string>();
  const children = container.children.map((root) => {
    const base = (mode === "id" ? root.source_label : root.top_name) ?? root.name;
    const name = distinctLabel(base, taken);
    taken.add(name);
    return name === root.name ? root : { ...root, name };
  });
  return { ...container, children };
}

export function readRootLabelMode(): RootLabelMode {
  try {
    return globalThis.localStorage?.getItem(STORAGE_KEY) === "id" ? "id" : "name";
  } catch {
    return "name";
  }
}

export function writeRootLabelMode(mode: RootLabelMode): void {
  try {
    globalThis.localStorage?.setItem(STORAGE_KEY, mode);
  } catch {
    // A preference that does not stick is still applied for this session.
  }
}
