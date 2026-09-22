/**
 * Read a `hierarchy.json` slice off the wire -- the TS half of
 * `ada/assets/projection.py`, and the same three rules:
 *
 *   1. REFUSE an unknown schema. A future document may give familiar columns a
 *      new meaning; reading the subset we recognise would be a guess.
 *   2. Index columns by NAME, never by position. `cols` is a header; a producer
 *      adding a column must not shift what an older reader sees.
 *   3. Accept `leaf` as 1 or true (and their string forms). Producers have
 *      emitted all of them, and a strict reader silently turns every node into
 *      a branch.
 *
 * Columnar on the wire because a 41k-row spine is ~2.6 MB that way; this is the
 * one place it is unpacked into objects, in one pass with the column positions
 * looked up once.
 */

import type { AssetNode, DeliveryKind, HierarchySlice, WireHierarchySlice } from "./types";

export const HIERARCHY_SCHEMA = "ada.assets/hierarchy@1";
export const BASE_COLS = ["id", "parent", "label", "kind", "leaf", "delivery"] as const;

export class HierarchyError extends Error {}

function asLeaf(v: unknown): boolean {
  if (typeof v === "boolean") return v;
  if (typeof v === "number") return v !== 0;
  if (typeof v === "string") return ["1", "true", "yes"].includes(v.trim().toLowerCase());
  return false;
}

function asDelivery(v: unknown): DeliveryKind {
  // "" on the wire is "no claim"; core's manifests spell the same thing "none".
  return v === "mesh" || v === "build" ? v : "none";
}

export function parseHierarchySlice(doc: WireHierarchySlice): HierarchySlice {
  if (!doc || typeof doc !== "object") throw new HierarchyError("hierarchy slice is not an object");
  if (doc.schema !== HIERARCHY_SCHEMA) {
    throw new HierarchyError(
      `unknown hierarchy schema ${JSON.stringify(doc.schema)}: this viewer reads ${HIERARCHY_SCHEMA} only`,
    );
  }
  const at = new Map<string, number>();
  doc.cols.forEach((c, i) => at.set(c, i));
  const missing = BASE_COLS.filter((c) => !at.has(c));
  if (missing.length) throw new HierarchyError(`hierarchy is missing column(s): ${missing.join(", ")}`);

  const iId = at.get("id")!;
  const iParent = at.get("parent")!;
  const iLabel = at.get("label")!;
  const iKind = at.get("kind")!;
  const iLeaf = at.get("leaf")!;
  const iDelivery = at.get("delivery")!;
  const iPath = at.get("path");
  const iProvider = at.get("provider");
  const width = doc.cols.length;

  const nodes: AssetNode[] = new Array(doc.rows.length);
  for (let r = 0; r < doc.rows.length; r++) {
    const row = doc.rows[r];
    if (row.length !== width) {
      throw new HierarchyError(`row ${r} has ${row.length} values but there are ${width} columns`);
    }
    const parent = row[iParent];
    const path = iPath === undefined ? undefined : row[iPath];
    const provider = iProvider === undefined ? null : row[iProvider];
    nodes[r] = {
      id: String(row[iId]),
      parent: parent === null || parent === undefined || parent === "" ? null : String(parent),
      label: String(row[iLabel] ?? row[iId]),
      kind: String(row[iKind] ?? ""),
      leaf: asLeaf(row[iLeaf]),
      delivery: asDelivery(row[iDelivery]),
      ...(typeof path === "string" && path ? { path } : {}),
      provider: typeof provider === "string" && provider ? provider : doc.provider,
    };
  }
  return {
    schema: HIERARCHY_SCHEMA,
    provider: doc.provider,
    collection: doc.collection,
    root: doc.root ?? null,
    producedAt: doc.produced_at,
    depth: doc.depth,
    nodes,
  };
}
