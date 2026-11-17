// Which SOURCE each saved-group member lives in, and what a group's load puts in the scene for it
// -- pure, so it runs under plain node.
//
// A SOURCE is the model a member was stored against: a published node or a file. A member that is
// the whole model loads that model. A member that is ONE ELEMENT of it loads alone where the
// platform can deliver a node by itself (a `build` claim is scoped to the node asked for, so the
// element's own row of the published tree is its own model); where it cannot (a `mesh` claim or a
// file is one document, and an element the published tree does not place has no row to ask for)
// the source is loaded once and everything in it that is not a member is hidden.

import { memberKey, type GroupMember, type GroupMemberTarget } from "./savedGroups";

type NodeTarget = Extract<GroupMemberTarget, { kind: "node" }>;

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

/** One element loaded alone: its own row of the published tree, as its own model. */
export interface MemberLoad {
  readonly key: string;
  /** The member's target with `node` set to the element's row. */
  readonly target: NodeTarget;
  /** The element's name: what its model is called in the scene. */
  readonly label: string;
  /** The members this one model stands for (several can name one row). */
  readonly members: readonly GroupMember[];
}

/** One source loaded as the document it is. */
export interface WholeLoad {
  readonly key: string;
  readonly target: GroupMemberTarget;
  readonly members: readonly GroupMember[];
  /** The members to leave shown, everything else in the model hidden; `null` when the whole
   *  model is itself a member, so nothing is hidden. */
  readonly only: readonly GroupMember[] | null;
}

export interface GroupLoadPlan {
  readonly alone: readonly MemberLoad[];
  readonly whole: readonly WholeLoad[];
}

/** What to load for `members` (the ones not in the scene yet).
 *
 *  `rows` is `memberKey` -> the element's row id in the published tree, for the members that could
 *  be placed; `perNode(sourceKey)` says whether that source's provider delivers a node by itself.
 *  Per source: a whole-model member loads the model, nothing hidden. Otherwise each element with a
 *  row, in a per-node source, loads alone (one model per row); the rest load the source once and
 *  show only themselves. An element whose row is the source's own node is the latter: its model is
 *  the source. */
export function planGroupLoads(
  members: readonly GroupMember[],
  rows: ReadonlyMap<string, string>,
  perNode: (sourceKey: string) => boolean,
): GroupLoadPlan {
  const alone = new Map<string, { key: string; target: NodeTarget; label: string; members: GroupMember[] }>();
  const whole: WholeLoad[] = [];
  for (const [key, list] of sourcesToLoad(members)) {
    const target = list[0].target;
    if (list.some((m) => m.element === null)) {
      whole.push({ key, target, members: list, only: null });
      continue;
    }
    const rest: GroupMember[] = [];
    for (const m of list) {
      const row = target.kind === "node" && perNode(key) ? rows.get(memberKey(m)) : undefined;
      if (target.kind !== "node" || !row || row === (target.node ?? target.subject) || m.element === null) {
        rest.push(m);
        continue;
      }
      const rowKey = `node:${target.provider}/${target.collection}/${row}`;
      const entry = alone.get(rowKey);
      if (entry) entry.members.push(m);
      else alone.set(rowKey, { key: rowKey, target: { ...target, node: row }, label: m.element, members: [m] });
    }
    if (rest.length) whole.push({ key, target, members: rest, only: rest });
  }
  return { alone: [...alone.values()], whole };
}

/** A node of a collection as its attribute index lists it (`POST .../search/nodes`, narrowed). */
export interface IndexedName {
  readonly node_id: string;
  readonly full_name: string | null;
  readonly provider?: string;
  readonly providers?: readonly string[];
}

/** `memberKey` -> row id for the element members of ONE collection that its attribute index names
 *  exactly once: an element is looked up by its full name, and a name that two nodes carry (or
 *  that only another provider's node carries) places nothing, rather than a guess. */
export function rowsFromIndex(members: readonly GroupMember[], nodes: readonly IndexedName[]): Map<string, string> {
  const byName = new Map<string, IndexedName[]>();
  for (const n of nodes) {
    if (!n.full_name) continue;
    const list = byName.get(n.full_name);
    if (list) list.push(n);
    else byName.set(n.full_name, [n]);
  }
  const out = new Map<string, string>();
  for (const m of members) {
    const t = m.target;
    if (t.kind !== "node" || m.element === null) continue;
    const named = (byName.get(m.element) ?? []).filter((n) => {
      const providers = n.providers ?? (n.provider ? [n.provider] : null);
      return !providers || providers.includes(t.provider);
    });
    const ids = new Set(named.map((n) => n.node_id));
    if (ids.size === 1) out.set(memberKey(m), [...ids][0]);
  }
  return out;
}
