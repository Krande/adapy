// Which SOURCE each saved-group member lives in -- pure, so it runs under plain node. A group loads
// one model per source: several members commonly share a published node or a file.

import type { GroupMember } from "./savedGroups";

/** The key one source is loaded under -- members sharing it load once. */
export function groupSourceKey(m: GroupMember): string {
  const t = m.target;
  return t.kind === "file" ? `file:${t.source_key}` : `node:${t.provider}/${t.collection}/${t.node ?? t.subject}`;
}

/** `members` grouped by source, in first-seen order. */
export function sourcesToLoad(members: readonly GroupMember[]): Map<string, GroupMember[]> {
  const out = new Map<string, GroupMember[]>();
  for (const m of members) {
    const key = groupSourceKey(m);
    out.set(key, [...(out.get(key) ?? []), m]);
  }
  return out;
}
