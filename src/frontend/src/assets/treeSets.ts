// Tree sets: NAMED lists of branches of one collection's tree -- "Main", "Alt design" -- that the
// Sources tab can be narrowed to, for everyone in the scope.
//
// WHAT A MEMBER IS. A row of the published tree, by its node id: ids are the provider's own and
// survive a reload and a re-publish, unlike anything the viewer assigns. The member also keeps the
// row's label (to name it when it is not in the tree being drawn) and the ids ABOVE it, root
// first: the tree is fetched a level at a time, and without them a filter could not keep the way
// down to a member whose branch nobody has opened yet.
//
// WHAT THE FILTER DRAWS. A member with everything under it, and the rows on the way down to it --
// nothing beside them. A member that is not in the tree (published by none of the revisions
// shown, or not fetched yet) draws nothing and is reported, not guessed at.
//
// THE DOCUMENT is one blob per collection, `assets/_sets/<collection>.json`, beside the saved view
// (`assets/_view/`) and for the same reason: it belongs to the scope's data. It is a document of
// its own rather than fields on the view, because a view save rewrites the whole view document
// and a viewer that did not know the fields would drop everyone's sets. Every change is a
// read-modify-write against the latest stored document, like saved groups.
//
// Pure: no React, no store, no fetch -- runs under `node --test`.

import type { Hierarchy } from "@/assets/hierarchy";

export const SETS_SCHEMA = "ada.assets/sets@1";

export function setsDocKey(collection: string): string {
  // A collection is one key segment already; the blob URL encodes the path.
  return `assets/_sets/${collection}.json`;
}

export interface TreeSetMember {
  readonly id: string;
  readonly label: string;
  /** Ids above the row, root first; the row itself is not in it. */
  readonly path: readonly string[];
  /** Whose geometry "Load set" loads for this member: absent is every provider that published
   *  some, a list is those only -- one provider's, or both side by side. */
  readonly providers?: readonly string[];
}

/** Does `member` load `provider`'s geometry? */
export function loadsProvider(member: TreeSetMember, provider: string): boolean {
  return !member.providers || member.providers.includes(provider);
}

export interface TreeSet {
  readonly id: string;
  readonly name: string;
  readonly created_at: string;
  readonly created_by?: string;
  readonly updated_at?: string;
  readonly members: readonly TreeSetMember[];
}

export interface TreeSetsDoc {
  readonly schema: typeof SETS_SCHEMA;
  readonly sets: readonly TreeSet[];
}

export const EMPTY_SETS_DOC: TreeSetsDoc = { schema: SETS_SCHEMA, sets: [] };

export class TreeSetsDocError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "TreeSetsDocError";
  }
}

function isRecord(v: unknown): v is Record<string, unknown> {
  return !!v && typeof v === "object" && !Array.isArray(v);
}

function str(v: unknown): string | null {
  return typeof v === "string" && v.length > 0 ? v : null;
}

function parseMember(raw: unknown): TreeSetMember | null {
  if (!isRecord(raw)) return null;
  const id = str(raw.id);
  if (!id) return null;
  const strings = (v: unknown) => (Array.isArray(v) ? v.filter((p): p is string => typeof p === "string" && p.length > 0) : null);
  const path = strings(raw.path) ?? [];
  // An empty list is kept: "no provider" is a choice (the member narrows the tree, loads nothing).
  const providers = strings(raw.providers);
  return { id, label: str(raw.label) ?? id, path, ...(providers ? { providers: [...new Set(providers)] } : {}) };
}

function parseSet(raw: unknown): TreeSet | null {
  if (!isRecord(raw)) return null;
  const id = str(raw.id);
  const name = typeof raw.name === "string" ? raw.name.trim() : "";
  if (!id || !name) return null;
  const members: TreeSetMember[] = [];
  const seen = new Set<string>();
  for (const m of Array.isArray(raw.members) ? raw.members : []) {
    const parsed = parseMember(m);
    if (!parsed || seen.has(parsed.id)) continue;
    seen.add(parsed.id);
    members.push(parsed);
  }
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
 *  Empty text reads as no sets. An UNKNOWN SCHEMA THROWS: the document is shared, and a viewer
 *  that read a newer one as "mostly fine" would write its own reading back over everybody's sets.
 *  Inside a known schema a malformed set or member is dropped, not guessed at. */
export function parseSetsDoc(raw: unknown): TreeSetsDoc {
  let value: unknown = raw;
  if (typeof raw === "string") {
    if (!raw.trim()) return EMPTY_SETS_DOC;
    try {
      value = JSON.parse(raw);
    } catch {
      throw new TreeSetsDocError("the tree sets document is not JSON");
    }
  }
  if (value === null || value === undefined) return EMPTY_SETS_DOC;
  if (!isRecord(value)) throw new TreeSetsDocError("the tree sets document is not a JSON object");
  if (value.schema !== SETS_SCHEMA) {
    throw new TreeSetsDocError(
      `the tree sets document is ${JSON.stringify(value.schema)}; this viewer reads ${SETS_SCHEMA} only, ` +
        "and will not write over a document it cannot fully read",
    );
  }
  const sets: TreeSet[] = [];
  const seen = new Set<string>();
  for (const s of Array.isArray(value.sets) ? value.sets : []) {
    const parsed = parseSet(s);
    if (!parsed || seen.has(parsed.id)) continue;
    seen.add(parsed.id);
    sets.push(parsed);
  }
  return { schema: SETS_SCHEMA, sets };
}

export function serialiseSetsDoc(doc: TreeSetsDoc): string {
  return JSON.stringify({ schema: SETS_SCHEMA, sets: doc.sets });
}

// --- changes: applied to the LATEST stored document, never to a stale copy ------------------

export type TreeSetsChange =
  | { readonly kind: "put"; readonly set: TreeSet }
  | { readonly kind: "rename"; readonly id: string; readonly name: string; readonly at: string }
  | { readonly kind: "delete"; readonly id: string }
  | { readonly kind: "add"; readonly id: string; readonly members: readonly TreeSetMember[]; readonly at: string }
  | { readonly kind: "remove"; readonly id: string; readonly memberIds: readonly string[]; readonly at: string }
  | {
      readonly kind: "providers";
      readonly id: string;
      /** Each member's new choice -- `null` is every provider again -- in one write. */
      readonly choices: readonly ProviderChoice[];
      readonly at: string;
    };

export interface ProviderChoice {
  readonly memberId: string;
  readonly providers: readonly string[] | null;
}

/** `doc` with one change applied; every other set is kept as stored. A change to a set someone
 *  else deleted in the meantime is a no-op rather than a resurrection. Adding a member that is
 *  already in refreshes its label and path and keeps its provider choice. */
export function applySetsChange(doc: TreeSetsDoc, change: TreeSetsChange): TreeSetsDoc {
  const edit = (id: string, f: (s: TreeSet) => TreeSet): TreeSetsDoc => ({
    schema: SETS_SCHEMA,
    sets: doc.sets.map((s) => (s.id === id ? f(s) : s)),
  });
  switch (change.kind) {
    case "put": {
      const i = doc.sets.findIndex((s) => s.id === change.set.id);
      const sets = [...doc.sets];
      if (i < 0) sets.push(change.set);
      else sets[i] = change.set;
      return { schema: SETS_SCHEMA, sets };
    }
    case "rename": {
      const name = change.name.trim();
      if (!name) return doc;
      return edit(change.id, (s) => ({ ...s, name, updated_at: change.at }));
    }
    case "delete":
      return { schema: SETS_SCHEMA, sets: doc.sets.filter((s) => s.id !== change.id) };
    case "add":
      return edit(change.id, (s) => {
        const incoming = new Map(change.members.map((m) => [m.id, m]));
        const kept = s.members.map((m) => {
          const fresh = incoming.get(m.id);
          if (!fresh) return m;
          return fresh.providers || !m.providers ? fresh : { ...fresh, providers: m.providers };
        });
        const have = new Set(s.members.map((m) => m.id));
        const added = [...incoming.values()].filter((m) => !have.has(m.id));
        return { ...s, members: [...kept, ...added], updated_at: change.at };
      });
    case "remove": {
      const drop = new Set(change.memberIds);
      return edit(change.id, (s) => ({ ...s, members: s.members.filter((m) => !drop.has(m.id)), updated_at: change.at }));
    }
    case "providers": {
      const choice = new Map(change.choices.map((c) => [c.memberId, c.providers]));
      return edit(change.id, (s) => ({
        ...s,
        members: s.members.map((m) => {
          if (!choice.has(m.id)) return m;
          const chosen = choice.get(m.id);
          const providers = chosen ? [...new Set(chosen)].sort() : null;
          const bare: TreeSetMember = { id: m.id, label: m.label, path: m.path };
          return providers ? { ...bare, providers } : bare;
        }),
        updated_at: change.at,
      }));
    }
  }
}

/** A fresh set id: time-ordered, random enough that two people creating one at once differ. */
export function newSetId(now: number = Date.now(), random: () => number = Math.random): string {
  const r = Math.floor(random() * 36 ** 6)
    .toString(36)
    .padStart(6, "0");
  return `s${now.toString(36).padStart(9, "0")}${r}`;
}

// --- capture: tree rows -> members ----------------------------------------------------------

/** Members for `ids`, with each row's label and the ids above it as the tree holds them now.
 *  An id the tree does not hold is skipped: there is no label or path to store for it. */
export function membersForIds<T extends { readonly label?: string }>(
  ids: Iterable<string>,
  h: Hierarchy<T>,
): TreeSetMember[] {
  const out: TreeSetMember[] = [];
  const seen = new Set<string>();
  for (const id of ids) {
    const node = h.byId.get(id);
    if (!node || seen.has(id)) continue;
    seen.add(id);
    const path: string[] = [];
    let parent = node.parent;
    let guard = 0;
    while (parent && guard++ < 10_000) {
      path.push(parent);
      parent = h.byId.get(parent)?.parent ?? null;
    }
    path.reverse();
    out.push({ id, label: node.data.label || id, path });
  }
  return out;
}

// --- the filter ------------------------------------------------------------------------------

export interface TreeSetFilter {
  /** For `displayHierarchy`'s `keep`: is the row a member, under one, or on the way down to one? */
  readonly keep: (id: string) => boolean;
  /** Members the tree does not hold -- nothing is drawn for them. */
  readonly missing: readonly TreeSetMember[];
}

export function treeSetFilter<T>(set: TreeSet, h: Hierarchy<T>): TreeSetFilter {
  const members = new Set(set.members.map((m) => m.id));
  // The way down: the stored paths (good before a branch is opened) and the tree's own parents
  // (good after a re-publish moved a member).
  const above = new Set<string>();
  for (const m of set.members) {
    for (const p of m.path) above.add(p);
    let parent = h.byId.get(m.id)?.parent ?? null;
    let guard = 0;
    while (parent && guard++ < 10_000) {
      above.add(parent);
      parent = h.byId.get(parent)?.parent ?? null;
    }
  }
  const under = new Map<string, boolean>();
  const isUnder = (id: string): boolean => {
    const trail: string[] = [];
    let cur: string | null = id;
    let answer = false;
    let guard = 0;
    while (cur && guard++ < 10_000) {
      const known = under.get(cur);
      if (known !== undefined) {
        answer = known;
        break;
      }
      if (members.has(cur)) {
        answer = true;
        break;
      }
      trail.push(cur);
      cur = h.byId.get(cur)?.parent ?? null;
    }
    for (const t of trail) under.set(t, answer);
    return answer;
  };
  return {
    keep: (id) => members.has(id) || above.has(id) || isUnder(id),
    missing: set.members.filter((m) => !h.byId.has(m.id)),
  };
}

/** Where a row stands in a set, for marking membership in the tree while the set is edited:
 *  - `member`    it is one;
 *  - `covered`   a member above it already holds it (removing it alone is not possible);
 *  - `contains`  members sit somewhere below it;
 *  - null        none of these. */
export type SetMembership = "member" | "covered" | "contains" | null;

export function setMembership<T>(set: TreeSet, h: Hierarchy<T>): (id: string) => SetMembership {
  const members = new Set(set.members.map((m) => m.id));
  const above = new Set<string>();
  for (const m of set.members) {
    for (const p of m.path) above.add(p);
    let parent = h.byId.get(m.id)?.parent ?? null;
    let guard = 0;
    while (parent && guard++ < 10_000) {
      above.add(parent);
      parent = h.byId.get(parent)?.parent ?? null;
    }
  }
  const covered = new Map<string, boolean>();
  const isCovered = (id: string): boolean => {
    // Strictly below a member: walk the parents, memoised along the way.
    const trail: string[] = [];
    let cur: string | null = h.byId.get(id)?.parent ?? null;
    let answer = false;
    let guard = 0;
    while (cur && guard++ < 10_000) {
      const known = covered.get(cur);
      if (known !== undefined) {
        answer = known;
        break;
      }
      if (members.has(cur)) {
        answer = true;
        break;
      }
      trail.push(cur);
      cur = h.byId.get(cur)?.parent ?? null;
    }
    // `trail` holds ancestors; each is covered iff something above IT is a member, which is
    // `answer` for all of them except where a member sits between -- handled by stopping there.
    for (const t of trail) covered.set(t, answer && !members.has(t));
    return answer;
  };
  return (id) => (members.has(id) ? "member" : isCovered(id) ? "covered" : above.has(id) ? "contains" : null);
}

/** Both filters, when both are on. */
export function bothKeep(
  a: ((id: string) => boolean) | undefined,
  b: ((id: string) => boolean) | undefined,
): ((id: string) => boolean) | undefined {
  if (!a) return b;
  if (!b) return a;
  return (id) => a(id) && b(id);
}
