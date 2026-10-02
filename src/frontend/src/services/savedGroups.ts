// Where saved groups live: one blob per scope, `_groups/groups.json` (`@/utils/groups/savedGroups`).
//
// A blob in the scope rather than a setting, for the reason `services/assetView.ts` gives for the
// saved tree view: it belongs to the scope's data and to everyone in it, and a setting is global
// and admin-only. The server keeps `_groups/` out of file listings.
//
// WITHOUT A SERVER (a notebook, a file opened from disk) there is no scope to share with, so the
// document is kept in this browser's localStorage instead -- per scope name, best effort, and a
// storage that throws (a private window) reads as no groups rather than taking the panel down.

import { runtime } from "@/runtime/config";
import { ApiError, type ScopeUrl } from "@/services/api/client";
import { filesApi } from "@/services/api/files";
import {
  applyGroupsChange,
  EMPTY_GROUPS_DOC,
  GROUPS_BLOB_KEY,
  parseGroupsDoc,
  serialiseGroupsDoc,
  type GroupsChange,
  type GroupsDoc,
} from "@/utils/groups/savedGroups";

const LOCAL_KEY_PREFIX = "ada.viewer.groups:";

/** Whether groups are shared through the server (REST mode) or kept in this browser. */
export function groupsAreShared(): boolean {
  return runtime.isRestMode();
}

/** The stored document. Nothing stored yet is an empty document; any OTHER failure throws, so a
 *  change is never written over a document that could not be read. */
export async function readGroupsDoc(scope: string): Promise<GroupsDoc> {
  if (!groupsAreShared()) {
    let text: string | null = null;
    try {
      text = window.localStorage.getItem(LOCAL_KEY_PREFIX + scope);
    } catch {
      text = null;
    }
    return text ? parseGroupsDoc(text) : EMPTY_GROUPS_DOC;
  }
  let bytes: ArrayBuffer;
  try {
    bytes = await filesApi.getBlob(scope as ScopeUrl, GROUPS_BLOB_KEY);
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) return EMPTY_GROUPS_DOC;
    throw e;
  }
  return parseGroupsDoc(new TextDecoder().decode(bytes));
}

async function writeGroupsDoc(scope: string, doc: GroupsDoc): Promise<void> {
  const text = serialiseGroupsDoc(doc);
  if (!groupsAreShared()) {
    try {
      window.localStorage.setItem(LOCAL_KEY_PREFIX + scope, text);
    } catch (e) {
      throw new Error(`this browser would not store the groups: ${e instanceof Error ? e.message : String(e)}`);
    }
    return;
  }
  await filesApi.putBlob(scope as ScopeUrl, GROUPS_BLOB_KEY, new Blob([text], { type: "application/json" }));
}

/** Apply one change to the LATEST stored document and write it back; returns what was written.
 *  Read right before the write, so groups someone else saved since this viewer last looked are
 *  kept -- the window for a lost update is the length of one request, not of a session. */
export async function changeGroups(scope: string, change: GroupsChange): Promise<GroupsDoc> {
  const latest = await readGroupsDoc(scope);
  const next = applyGroupsChange(latest, change);
  await writeGroupsDoc(scope, next);
  return next;
}
