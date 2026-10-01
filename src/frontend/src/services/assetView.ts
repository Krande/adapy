// The saved tree view for one collection in one scope: `assets/_view/<collection>.json`.
//
// A blob in the scope rather than a setting, because it belongs to the scope's
// data -- it travels with it, and anyone who can publish there can curate how it
// is drawn -- and a setting is global and admin-only. Under `assets/_view/`
// because a leading `_` is core's in the asset key grammar: the index skips it,
// so the file is never mistaken for an asset or reported as a malformed key.
//
// Shared by everyone in the scope; last write wins. It changes what is DRAWN,
// never what is published or who may see it.

import { filesApi } from "@/services/api/files";
import type { ScopeUrl } from "@/services/api/client";
import { parseViewDoc, type TreeViewDoc } from "@/assets/treeView";

export function viewDocKey(collection: string): string {
  // A collection is a key segment already -- no '/', nothing to escape -- and the
  // blob URL encodes the path.
  return `assets/_view/${collection}.json`;
}

/** The saved view, or null when nothing is saved. Any failure reads as nothing
 *  saved: a view that cannot be read must not stop the tree being drawn. */
export async function readViewDoc(scope: string, collection: string): Promise<TreeViewDoc | null> {
  try {
    const bytes = await filesApi.getBlob(scope as ScopeUrl, viewDocKey(collection));
    return parseViewDoc(new TextDecoder().decode(bytes));
  } catch {
    return null;
  }
}

export async function writeViewDoc(scope: string, collection: string, doc: TreeViewDoc): Promise<void> {
  await filesApi.putBlob(
    scope as ScopeUrl,
    viewDocKey(collection),
    new Blob([JSON.stringify(doc)], { type: "application/json" }),
  );
}
