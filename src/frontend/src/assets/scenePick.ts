// From an element picked in the 3D view to the Sources-tree row it came from.
//
// NOTHING IN THE GLB NAMES A ROW. A model loaded from a provider is a GLB whose scene tree is
// built from its `id_hierarchy` -- a name and a parent per node -- and no row id survives the
// trip. What does survive is the NAME PATH: the picked node's names from the model's top down.
// The loaded source name says which row the model was loaded at (`NodeRef`: the node, else the
// subject), so the walk starts there and descends one level per path name, matching a child by
// its label.
//
// TWO SHAPES OF PATH, both real:
//   - it starts AT the anchor (the model's top is the row it was loaded from), or ABOVE it (a
//     model loaded at an area still carries the site above it). Path names before the one that
//     names the anchor are skipped; when none names it, the path is taken to start just below.
//   - a name can carry a ` [<row id>]` suffix, which a producer adds where siblings share a
//     label. The id is then the answer outright; the label without it still matches.
//
// The walk stops at the deepest match rather than failing: a partial reveal (the subject, a deck
// above the picked plate) is still the right place in the tree, and an empty one helps nobody.
//
// React-free and store-free: the tree is reached through `PickTreeSource`, so the walk runs
// under `node --test` against a canned forest, and the caller decides how a level is fetched.

/** Trimmed, without a leading `/` (some producers write absolute-looking names). */
export function normalizeName(name: string): string {
  const t = name.trim();
  return t.startsWith("/") ? t.slice(1).trim() : t;
}

const ID_SUFFIX_RE = /^(.*\S)\s+\[([^\]\s]+)\]$/;

/** `"Deck A [n42]"` -> `{ base: "Deck A", id: "n42" }`; no suffix -> `id: null`. */
export function splitIdSuffix(name: string): { base: string; id: string | null } {
  const n = normalizeName(name);
  const m = ID_SUFFIX_RE.exec(n);
  return m ? { base: normalizeName(m[1]), id: m[2] } : { base: n, id: null };
}

export interface PickRow {
  readonly id: string;
  readonly label: string;
}

/** The row among `rows` that `name` stands for, or null. In order of trust: an id suffix naming
 *  one of the rows; an exact label (either side's suffix stripped); then the same ignoring case.
 *  Among equals the first row wins -- the tree's own order. */
export function matchRow(rows: readonly PickRow[], name: string): PickRow | null {
  const want = splitIdSuffix(name);
  if (want.id !== null) {
    const byId = rows.find((r) => r.id === want.id);
    if (byId) return byId;
  }
  if (!want.base) return null;
  const labelOf = (r: PickRow) => splitIdSuffix(r.label);
  const exact = rows.find((r) => normalizeName(r.label) === want.base || labelOf(r).base === want.base);
  if (exact) return exact;
  const lower = want.base.toLowerCase();
  return (
    rows.find((r) => normalizeName(r.label).toLowerCase() === lower || labelOf(r).base.toLowerCase() === lower) ?? null
  );
}

/** Index of the first path name BELOW the anchor: one past the first entry naming the anchor, or
 *  0 when no entry does (the path starts below it). */
export function pathStartBelow(path: readonly string[], anchor: PickRow): number {
  for (let i = 0; i < path.length; i++) {
    if (matchRow([anchor], path[i])) return i + 1;
  }
  return 0;
}

/** The tree the walk reads. `ensureChildren` fetches a row's level below when it is not held yet
 *  (and resolves at once when it is); `children` and `label` are read AFTER it settles. */
export interface PickTreeSource {
  label(id: string): string | undefined;
  children(id: string): readonly string[];
  ensureChildren(id: string): Promise<void>;
  /** Checked between levels: false = a newer pick superseded this one, stop. */
  alive?(): boolean;
}

export interface PickResolution {
  /** The deepest row the path reached -- the one to select. */
  readonly row: string;
  /** Anchor first, down to `row` inclusive. */
  readonly chain: readonly string[];
  /** How many path names were matched below the anchor. */
  readonly matched: number;
}

/** Walk from `anchor` down `path` (names from the model's top to the picked element). */
export async function resolvePickRow(
  src: PickTreeSource,
  anchor: string,
  path: readonly string[],
): Promise<PickResolution | null> {
  const anchorLabel = src.label(anchor);
  if (anchorLabel === undefined) return null;
  const chain = [anchor];
  let cur = anchor;
  let matched = 0;
  const start = pathStartBelow(path, { id: anchor, label: anchorLabel });
  for (let i = start; i < path.length; i++) {
    await src.ensureChildren(cur);
    if (src.alive && !src.alive()) return null;
    const rows: PickRow[] = [];
    for (const id of src.children(cur)) {
      const label = src.label(id);
      if (label !== undefined) rows.push({ id, label });
    }
    let hit = matchRow(rows, path[i]);
    // No name in the path is the anchor's, so the first one may be a name the model's top only
    // has in the viewer (a source label); the anchor's children are then one name further on.
    if (!hit && start === 0 && i === 0 && path.length > 1) {
      hit = matchRow(rows, path[1]);
      if (hit) i = 1;
    }
    if (!hit) break;
    cur = hit.id;
    chain.push(cur);
    matched++;
  }
  return { row: cur, chain, matched };
}
