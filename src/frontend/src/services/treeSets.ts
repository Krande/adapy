// Where tree sets live: one blob per collection, `assets/_sets/<collection>.json`
// (`@/assets/treeSets`). In the scope, shared by everyone in it, like the saved view.

import { ApiError, type ScopeUrl } from "@/services/api/client";
import { filesApi } from "@/services/api/files";
import {
  applySetsChange,
  EMPTY_SETS_DOC,
  parseSetsDoc,
  serialiseSetsDoc,
  setsDocKey,
  type TreeSetsChange,
  type TreeSetsDoc,
} from "@/assets/treeSets";

/** The stored document. Nothing stored yet is an empty document; any OTHER failure throws, so a
 *  change is never written over a document that could not be read. */
export async function readSetsDoc(scope: string, collection: string): Promise<TreeSetsDoc> {
  let bytes: ArrayBuffer;
  try {
    bytes = await filesApi.getBlob(scope as ScopeUrl, setsDocKey(collection));
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) return EMPTY_SETS_DOC;
    throw e;
  }
  return parseSetsDoc(new TextDecoder().decode(bytes));
}

/** Apply one change to the LATEST stored document and write it back; returns what was written. */
export async function changeSets(scope: string, collection: string, change: TreeSetsChange): Promise<TreeSetsDoc> {
  const next = applySetsChange(await readSetsDoc(scope, collection), change);
  await filesApi.putBlob(
    scope as ScopeUrl,
    setsDocKey(collection),
    new Blob([serialiseSetsDoc(next)], { type: "application/json" }),
  );
  return next;
}
