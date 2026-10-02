// Saved groups: a NAMED set of elements and levels, picked across every loaded model, that
// everyone in the scope sees and that survives a reload.
//
// WHAT A MEMBER IS. Nothing the viewer holds at runtime outlives a reload -- tree row ids and
// range ids are assigned when a model is loaded -- so a member is stored the way the server finds
// a selection again (`utils/export/selectionExport.ts`): WHICH MODEL (a file core reads, or a
// published node its provider reads), the row's NAME, and the names above it down from the
// model's root row. Names repeat; the path is what tells two same-named plates in two decks
// apart. A model root selected whole is a member with `element: null` -- the whole source/node.
//
// THE WIRE SHAPE IS FIXED. The same member objects are sent as a clash check's `group` body, and
// the backend reads exactly `{target, element, path}` with the target in snake_case.
//
// CAPTURE COLLAPSES. A tree click on a level selects every leaf range below it; storing those
// leaves would turn one deck into thousands of members, and a member list that no longer says
// "this deck". So a selection is folded up to the highest rows whose whole subtree is selected.
//
// THE DOCUMENT is one blob per scope, `_groups/groups.json`, shared by everyone in it. Every
// change is a read-modify-write against the latest stored document, so two people editing
// different groups do not undo each other; the last write wins per group.
//
// React-free and store-free (types only), so it runs under `node --test`; the stores and the
// panel assemble the live dependencies.

import type { NodeRef } from "@/assets/delivery";
import type { TreeNodeData } from "@/components/tree_view/CustomNode";
import { elementPath as pathBelow, modelRootOf, parseAssetSourceName } from "@/utils/export/selectionExport";
import type { TreeIndices } from "@/utils/tree_view/treeGraph";

export const GROUPS_SCHEMA = "ada.viewer/groups@1";

/** The scope blob holding every saved group. A leading `_` keeps it out of file listings. */
export const GROUPS_BLOB_KEY = "_groups/groups.json";

// --- the wire / stored shapes --------------------------------------------------------------

export type GroupMemberTarget =
  | { readonly kind: "file"; readonly source_key: string }
  | {
      readonly kind: "node";
      readonly provider: string;
      readonly collection: string;
      readonly subject: string;
      readonly revision: string | null;
      /** The node the model was loaded at, when it is not the subject itself. */
      readonly node: string | null;
    };

export interface GroupMember {
  readonly target: GroupMemberTarget;
  /** The row's name; `null` for a model's root row, which means the whole model. */
  readonly element: string | null;
  /** Row names from below the model's root row down to `element`, inclusive. */
  readonly path: readonly string[];
}

export interface SavedGroup {
  readonly id: string;
  readonly name: string;
  readonly created_at: string;
  readonly created_by?: string;
  readonly updated_at?: string;
  readonly members: readonly GroupMember[];
}

export interface GroupsDoc {
  readonly schema: typeof GROUPS_SCHEMA;
  readonly groups: readonly SavedGroup[];
}

export const EMPTY_GROUPS_DOC: GroupsDoc = { schema: GROUPS_SCHEMA, groups: [] };

export class GroupsDocError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "GroupsDocError";
  }
}

function isRecord(v: unknown): v is Record<string, unknown> {
  return !!v && typeof v === "object" && !Array.isArray(v);
}

function str(v: unknown): string | null {
  return typeof v === "string" && v.length > 0 ? v : null;
}

/** One member, or null when it is not the known shape (dropped rather than guessed at). */
export function parseMember(raw: unknown): GroupMember | null {
  if (!isRecord(raw) || !isRecord(raw.target)) return null;
  const t = raw.target;
  let target: GroupMemberTarget;
  if (t.kind === "file") {
    const sourceKey = str(t.source_key);
    if (!sourceKey) return null;
    target = { kind: "file", source_key: sourceKey };
  } else if (t.kind === "node") {
    const provider = str(t.provider);
    const collection = str(t.collection);
    const subject = str(t.subject);
    if (!provider || !collection || !subject) return null;
    target = { kind: "node", provider, collection, subject, revision: str(t.revision), node: str(t.node) };
  } else {
    return null;
  }
  const element = raw.element === null || raw.element === undefined ? null : str(raw.element);
  if (raw.element !== null && raw.element !== undefined && element === null) return null;
  const path = Array.isArray(raw.path) ? raw.path.filter((p): p is string => typeof p === "string") : [];
  if (element === null && path.length > 0) return null;
  return { target, element, path };
}

function parseGroup(raw: unknown): SavedGroup | null {
  if (!isRecord(raw)) return null;
  const id = str(raw.id);
  const name = typeof raw.name === "string" ? raw.name.trim() : "";
  if (!id || !name) return null;
  const members = (Array.isArray(raw.members) ? raw.members : [])
    .map(parseMember)
    .filter((m): m is GroupMember => m !== null);
  const createdBy = str(raw.created_by);
  const updatedAt = str(raw.updated_at);
  return {
    id,
    name,
    created_at: str(raw.created_at) ?? "",
    ...(createdBy ? { created_by: createdBy } : {}),
    ...(updatedAt ? { updated_at: updatedAt } : {}),
    members,
  };
}

/** Parse the stored document (its JSON text, or an already-decoded value).
 *
 *  Empty text reads as no groups. An UNKNOWN SCHEMA THROWS: the document is shared, and a viewer
 *  that read a newer one as "mostly fine" would write its own reading back over everybody's
 *  groups. Inside a known schema, a malformed group or member is dropped, not guessed at. */
export function parseGroupsDoc(raw: unknown): GroupsDoc {
  let value: unknown = raw;
  if (typeof raw === "string") {
    if (!raw.trim()) return EMPTY_GROUPS_DOC;
    try {
      value = JSON.parse(raw);
    } catch {
      throw new GroupsDocError("the saved groups document is not JSON");
    }
  }
  if (value === null || value === undefined) return EMPTY_GROUPS_DOC;
  if (!isRecord(value)) throw new GroupsDocError("the saved groups document is not a JSON object");
  if (value.schema !== GROUPS_SCHEMA) {
    throw new GroupsDocError(
      `the saved groups document is ${JSON.stringify(value.schema)}; this viewer reads ${GROUPS_SCHEMA} only, ` +
        "and will not write over a document it cannot fully read",
    );
  }
  const groups: SavedGroup[] = [];
  const seen = new Set<string>();
  for (const g of Array.isArray(value.groups) ? value.groups : []) {
    const parsed = parseGroup(g);
    if (!parsed || seen.has(parsed.id)) continue;
    seen.add(parsed.id);
    groups.push(parsed);
  }
  return { schema: GROUPS_SCHEMA, groups };
}

export function serialiseGroupsDoc(doc: GroupsDoc): string {
  return JSON.stringify({ schema: GROUPS_SCHEMA, groups: doc.groups });
}

// --- changes: applied to the LATEST stored document, never to a stale copy ------------------

export type GroupsChange =
  | { readonly kind: "put"; readonly group: SavedGroup }
  | { readonly kind: "rename"; readonly id: string; readonly name: string; readonly at: string }
  | { readonly kind: "delete"; readonly id: string };

/** `doc` with one change applied. Everything else in `doc` -- other people's groups, written
 *  since this viewer last read -- is kept as it is. A rename of a group someone else deleted in
 *  the meantime is a no-op rather than a resurrection. */
export function applyGroupsChange(doc: GroupsDoc, change: GroupsChange): GroupsDoc {
  switch (change.kind) {
    case "put": {
      const i = doc.groups.findIndex((g) => g.id === change.group.id);
      const groups = [...doc.groups];
      if (i < 0) groups.push(change.group);
      else groups[i] = change.group;
      return { schema: GROUPS_SCHEMA, groups };
    }
    case "rename": {
      const name = change.name.trim();
      if (!name) return doc;
      return {
        schema: GROUPS_SCHEMA,
        groups: doc.groups.map((g) => (g.id === change.id ? { ...g, name, updated_at: change.at } : g)),
      };
    }
    case "delete":
      return { schema: GROUPS_SCHEMA, groups: doc.groups.filter((g) => g.id !== change.id) };
  }
}

/** A fresh group id: time-ordered, with enough randomness that two people creating one in the
 *  same millisecond do not collide. */
export function newGroupId(now: number = Date.now(), random: () => number = Math.random): string {
  const r = Math.floor(random() * 36 ** 6)
    .toString(36)
    .padStart(6, "0");
  // Fixed-width time, so ids sort in creation order as plain strings.
  return `g${now.toString(36).padStart(9, "0")}${r}`;
}

// --- capture: a selection -> members ---------------------------------------------------------

/** What the asset browser recorded about a model it loaded (`LoadedAsset`, narrowed). */
export interface LoadedRef {
  readonly sourceName: string;
  readonly ref: NodeRef;
}

function refOfSourceName(sourceName: string, loaded: readonly LoadedRef[]): NodeRef | null {
  return loaded.find((a) => a.sourceName === sourceName)?.ref ?? parseAssetSourceName(sourceName);
}

/** The member target for a model loaded under `sourceName`, or null when the source name does not
 *  say where the model came from (an `assets:` name that does not parse). */
export function targetForSource(sourceName: string, loaded: readonly LoadedRef[]): GroupMemberTarget | null {
  if (!sourceName.startsWith("assets:")) return { kind: "file", source_key: sourceName };
  const ref = refOfSourceName(sourceName, loaded);
  if (!ref) return null;
  return {
    kind: "node",
    provider: ref.provider,
    collection: ref.collection,
    subject: ref.subject,
    revision: ref.revision || null,
    node: ref.node && ref.node !== ref.subject ? ref.node : null,
  };
}

function hasGeometry(row: TreeNodeData): boolean {
  return row.rangeId != null && !!row.node_name;
}

/** The highest rows whose WHOLE subtree is selected, model by model, in tree order.
 *
 *  `selected` holds the ids of the geometry-bearing rows the selection covers. A row is "whole"
 *  when it has geometry at or below it and every such row is selected. The walk descends only
 *  where a row is not whole, so a deck picked in the tree comes back as the deck, and a few
 *  picked plates come back as those plates. */
export function collapseSelectedRows(selected: ReadonlySet<string>, idx: TreeIndices): TreeNodeData[] {
  if (selected.size === 0) return [];
  // Post-order counts, iteratively: real trees are deep enough to make recursion a risk.
  const total = new Map<string, number>();
  const picked = new Map<string, number>();
  const roots = idx.root.children ?? [];
  const touched = new Set<string>();
  for (const id of selected) {
    const row = idx.byId.get(id);
    const root = row ? modelRootOf(row, idx) : null;
    if (root) touched.add(root.id);
  }
  for (const root of roots) {
    if (!touched.has(root.id)) continue;
    const stack: Array<[TreeNodeData, boolean]> = [[root, false]];
    while (stack.length) {
      const [row, expanded] = stack.pop()!;
      if (!expanded) {
        stack.push([row, true]);
        for (const child of row.children ?? []) stack.push([child, false]);
        continue;
      }
      let t = hasGeometry(row) ? 1 : 0;
      let p = hasGeometry(row) && selected.has(row.id) ? 1 : 0;
      for (const child of row.children ?? []) {
        t += total.get(child.id) ?? 0;
        p += picked.get(child.id) ?? 0;
      }
      total.set(row.id, t);
      picked.set(row.id, p);
    }
  }

  const out: TreeNodeData[] = [];
  for (const root of roots) {
    if (!touched.has(root.id)) continue;
    // Pre-order, children in tree order.
    const stack: TreeNodeData[] = [root];
    while (stack.length) {
      const row = stack.pop()!;
      const t = total.get(row.id) ?? 0;
      const p = picked.get(row.id) ?? 0;
      if (p === 0) continue;
      if (t > 0 && p === t) {
        out.push(row);
        continue;
      }
      const children = row.children ?? [];
      for (let i = children.length - 1; i >= 0; i--) stack.push(children[i]);
    }
  }
  return out;
}

export interface CapturedMembers {
  readonly members: GroupMember[];
  /** Rows left out, with why -- a model not loaded from storage has no source to name. */
  readonly skipped: readonly { readonly name: string; readonly reason: string }[];
}

/** Members for `rows` (already collapsed): one per row, de-duplicated. */
export function membersForRows(
  rows: readonly TreeNodeData[],
  idx: TreeIndices,
  loaded: readonly LoadedRef[],
): CapturedMembers {
  const members: GroupMember[] = [];
  const skipped: { name: string; reason: string }[] = [];
  const seen = new Set<string>();
  for (const row of rows) {
    const root = modelRootOf(row, idx);
    const sourceName = root?.source_name ?? null;
    if (!root || !sourceName) {
      skipped.push({ name: row.name, reason: "its model was not loaded from storage, so there is no source to name" });
      continue;
    }
    const target = targetForSource(sourceName, loaded);
    if (!target) {
      skipped.push({ name: row.name, reason: `the source name ${sourceName} does not say which published node it is` });
      continue;
    }
    const member: GroupMember =
      row.id === root.id ? { target, element: null, path: [] } : { target, element: row.name, path: pathBelow(row, root, idx) };
    const key = memberKey(member);
    if (seen.has(key)) continue;
    seen.add(key);
    members.push(member);
  }
  return { members, skipped };
}

/** A member's identity, for de-duplication. */
export function memberKey(m: GroupMember): string {
  return JSON.stringify([targetKey(m.target), m.element, m.path]);
}

function targetKey(t: GroupMemberTarget): string {
  return t.kind === "file"
    ? `file:${t.source_key}`
    : `node:${t.provider}/${t.collection}/${t.subject}@${t.revision ?? ""}#${t.node ?? ""}`;
}

// --- restore: members -> rows in what is loaded now ------------------------------------------

/** Whether the loaded model at `root` is the one `target` names. A node is matched by provider,
 *  collection, subject and node; a different REVISION of it still matches -- the names a member
 *  is found by rarely change between revisions, and refusing would make every group go stale at
 *  the next publish. */
export function rootMatchesTarget(root: TreeNodeData, target: GroupMemberTarget, loaded: readonly LoadedRef[]): boolean {
  const sourceName = root.source_name;
  if (!sourceName) return false;
  if (target.kind === "file") return sourceName === target.source_key;
  if (!sourceName.startsWith("assets:")) return false;
  const ref = refOfSourceName(sourceName, loaded);
  if (!ref) return false;
  return (
    ref.provider === target.provider &&
    ref.collection === target.collection &&
    ref.subject === target.subject &&
    (ref.node ?? ref.subject) === (target.node ?? target.subject)
  );
}

function endsWith(a: readonly string[], b: readonly string[]): boolean {
  if (b.length > a.length) return false;
  for (let i = 1; i <= b.length; i++) if (a[a.length - i] !== b[b.length - i]) return false;
  return true;
}

function sameList(a: readonly string[], b: readonly string[]): boolean {
  return a.length === b.length && endsWith(a, b);
}

export interface MemberResolution {
  /** The rows to select, subtrees included (the caller expands them). */
  readonly rows: TreeNodeData[];
  /** Members whose model is not loaded at all. */
  readonly notLoaded: GroupMember[];
  /** Members whose model is loaded but that name no row in it. */
  readonly notFound: GroupMember[];
}

/** Find each member's rows in the loaded scene.
 *
 *  `element: null` is the model's root row. Otherwise rows NAMED `element` whose path below the
 *  root matches -- exactly first; failing that, a path that ends with the stored one (the model
 *  is loaded from higher up now) or that the stored one ends with (from lower down). Several
 *  rows with the same name and path are all selected: the member cannot tell them apart either. */
export function resolveMembers(
  members: readonly GroupMember[],
  idx: TreeIndices,
  loaded: readonly LoadedRef[],
): MemberResolution {
  const roots = idx.root.children ?? [];
  const nameIndex = new Map<string, Map<string, TreeNodeData[]>>();
  const indexFor = (root: TreeNodeData) => {
    let byName = nameIndex.get(root.id);
    if (byName) return byName;
    byName = new Map();
    const stack: TreeNodeData[] = [...(root.children ?? [])];
    while (stack.length) {
      const row = stack.pop()!;
      const list = byName.get(row.name);
      if (list) list.push(row);
      else byName.set(row.name, [row]);
      for (const child of row.children ?? []) stack.push(child);
    }
    nameIndex.set(root.id, byName);
    return byName;
  };

  const rows: TreeNodeData[] = [];
  const seen = new Set<string>();
  const add = (r: TreeNodeData) => {
    if (seen.has(r.id)) return;
    seen.add(r.id);
    rows.push(r);
  };
  const notLoaded: GroupMember[] = [];
  const notFound: GroupMember[] = [];
  for (const member of members) {
    const matching = roots.filter((r) => rootMatchesTarget(r, member.target, loaded));
    if (matching.length === 0) {
      notLoaded.push(member);
      continue;
    }
    if (member.element === null) {
      matching.forEach(add);
      continue;
    }
    const exact: TreeNodeData[] = [];
    const loose: TreeNodeData[] = [];
    for (const root of matching) {
      for (const row of indexFor(root).get(member.element) ?? []) {
        const path = pathBelow(row, root, idx);
        if (sameList(path, member.path)) exact.push(row);
        else if (endsWith(path, member.path) || endsWith(member.path, path)) loose.push(row);
      }
    }
    const hits = exact.length ? exact : loose;
    if (hits.length === 0) notFound.push(member);
    else hits.forEach(add);
  }
  return { rows, notLoaded, notFound };
}

/** Every geometry-bearing row at or below `rows`, each once. */
export function geometryRowsUnder(rows: readonly TreeNodeData[]): TreeNodeData[] {
  const out: TreeNodeData[] = [];
  const seen = new Set<string>();
  const stack: TreeNodeData[] = [...rows];
  while (stack.length) {
    const row = stack.pop()!;
    if (seen.has(row.id)) continue;
    seen.add(row.id);
    if (hasGeometry(row)) out.push(row);
    for (const child of row.children ?? []) stack.push(child);
  }
  return out;
}

/** How a member reads in a list: its element (or the whole model) and where it lives. */
export function describeMember(m: GroupMember): string {
  const where = m.target.kind === "file" ? m.target.source_key.split("/").pop() ?? m.target.source_key : `${m.target.collection}/${m.target.node ?? m.target.subject}`;
  return m.element === null ? `${where} (whole model)` : `${m.element} — ${where}`;
}

// --- request geometry: members -> provider node ids ------------------------------------------

/** A node one collection's provider can be asked for. */
export interface GroupNodeTarget {
  readonly collection: string;
  readonly id: string;
  readonly label?: string;
}

export interface GroupNodePlan {
  /** collection -> the nodes to ask for, de-duplicated, in member order. */
  readonly byCollection: ReadonlyMap<string, readonly GroupNodeTarget[]>;
  /** Members that could not be turned into a node id, with why. */
  readonly unresolved: readonly { readonly member: GroupMember; readonly reason: string }[];
}

/** Which nodes a group's members name.
 *
 *  A whole published node is its own id. An element inside one needs its ROW id, which only the
 *  asset tree knows: `elementRows` holds what the caller managed to resolve, by `memberKey`. A
 *  member from a file names no published node at all.
 *
 *  An element's label is its own name. A whole node's is not stored in the member; `labels` (by
 *  `memberKey`) supplies it where the caller knows it -- the clash check's geometry plan reports the
 *  label each member was looked for under -- so a provider that finds nodes by label can be asked. */
export function groupNodePlan(
  members: readonly GroupMember[],
  elementRows: ReadonlyMap<string, string> = new Map(),
  labels: ReadonlyMap<string, string> = new Map(),
): GroupNodePlan {
  const byCollection = new Map<string, GroupNodeTarget[]>();
  const seen = new Set<string>();
  const unresolved: { member: GroupMember; reason: string }[] = [];
  for (const member of members) {
    const t = member.target;
    if (t.kind === "file") {
      unresolved.push({ member, reason: "it is in a file, not a published node a provider can be asked for" });
      continue;
    }
    let id: string | undefined;
    if (member.element === null) id = t.node ?? t.subject;
    else id = elementRows.get(memberKey(member));
    if (!id) {
      unresolved.push({ member, reason: "its row could not be found in the published tree" });
      continue;
    }
    const key = `${t.collection}\u0000${id}`;
    if (seen.has(key)) continue;
    seen.add(key);
    const list = byCollection.get(t.collection) ?? [];
    const label = member.element ?? labels.get(memberKey(member));
    list.push({ collection: t.collection, id, ...(label ? { label } : {}) });
    byCollection.set(t.collection, list);
  }
  return { byCollection, unresolved };
}
