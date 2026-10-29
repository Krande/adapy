// What a saved group holds, for the Saved groups details: its members by source, and by the kind
// of element each is (from the attribute index). React-free and store-free; the index read is
// handed in, so the counting runs under `node --test`.

import type { GroupMember } from "./savedGroups";

export interface CountedLabel {
  readonly label: string;
  readonly count: number;
}

/** `[label, count]` by count, then label. */
function counted(map: ReadonlyMap<string, number>): CountedLabel[] {
  return [...map.entries()]
    .map(([label, count]) => ({ label, count }))
    .sort((a, b) => b.count - a.count || a.label.localeCompare(b.label, undefined, { numeric: true }));
}

/** Where the members come from: `<provider> · <COLLECTION>` for a published node, the file's key
 *  for a file. */
export function memberSources(members: readonly GroupMember[]): CountedLabel[] {
  const by = new Map<string, number>();
  for (const m of members) {
    const label = m.target.kind === "node" ? `${m.target.provider} · ${m.target.collection.toUpperCase()}` : m.target.source_key;
    by.set(label, (by.get(label) ?? 0) + 1);
  }
  return counted(by);
}

/** How each node member is looked up in its collection's attribute index: an element by its full
 *  name, a whole node by its id. File members have no index and are left out. */
export function indexLookups(members: readonly GroupMember[]): Map<string, { nodeIds: string[]; fullNames: string[] }> {
  const out = new Map<string, { nodeIds: string[]; fullNames: string[] }>();
  for (const m of members) {
    if (m.target.kind !== "node") continue;
    const key = m.target.collection.toLowerCase();
    const entry = out.get(key) ?? { nodeIds: [], fullNames: [] };
    if (m.element) entry.fullNames.push(m.element);
    else entry.nodeIds.push(m.target.node ?? m.target.subject);
    out.set(key, entry);
  }
  return out;
}

export type KindRead = (collection: string, lookup: { nodeIds: string[]; fullNames: string[] }) => Promise<
  { node_id: string; full_name: string | null; kind: string | null }[] | null
>;

export interface KindSummary {
  /** Members by element kind, as the index names it (`BEND`, `PANE`, `GENSEC`, ...). */
  readonly kinds: CountedLabel[];
  /** Members whose kind the index does not give: not indexed, or from a file. */
  readonly unknown: number;
}

/** The members' kinds, one index read per collection. A read that fails counts its members as
 *  unknown rather than failing the summary. */
export async function memberKinds(members: readonly GroupMember[], read: KindRead): Promise<KindSummary> {
  const by = new Map<string, number>();
  let known = 0;
  for (const [collection, lookup] of indexLookups(members)) {
    let rows: Awaited<ReturnType<KindRead>> = null;
    try {
      rows = await read(collection, lookup);
    } catch {
      rows = null;
    }
    if (!rows) continue;
    const byName = new Map(rows.filter((r) => r.full_name).map((r) => [r.full_name as string, r.kind]));
    const byId = new Map(rows.map((r) => [r.node_id, r.kind]));
    const kinds = [
      ...lookup.fullNames.map((n) => byName.get(n) ?? null),
      ...lookup.nodeIds.map((id) => byId.get(id) ?? null),
    ];
    for (const kind of kinds) {
      if (!kind) continue;
      known++;
      by.set(kind, (by.get(kind) ?? 0) + 1);
    }
  }
  return { kinds: counted(by), unknown: members.length - known };
}
