// A synthetic large spine, shaped like the real worst case this module set is
// sized for: one root, forty branches, ten groups per branch, a hundred
// leaves per group -- ~40k rows, depth 3. Exported so the performance test
// and any future one can share it without regenerating fixtures by hand.

import { HIERARCHY_SCHEMA } from "../../assets/projection";
import type { WireHierarchySlice } from "../../assets/types";

export const SYNTHETIC_ROOT = "area-1";
const BRANCH_COUNT = 40;
const GROUPS_PER_BRANCH = 10;
const LEAVES_PER_GROUP = 100;

export interface SyntheticSpine {
  readonly wire: WireHierarchySlice;
  /** Every branch id, in generation order. */
  readonly branchIds: readonly string[];
  /** A sample of leaf ids spread across the tree, for spot manifests. */
  readonly sampleLeafIds: readonly string[];
  readonly rowCount: number;
}

export function buildSyntheticSpine(): SyntheticSpine {
  const cols = ["id", "parent", "label", "kind", "leaf", "delivery"];
  const rows: unknown[][] = [];
  const branchIds: string[] = [];
  const sampleLeafIds: string[] = [];

  rows.push([SYNTHETIC_ROOT, null, "Area 1", "area", 0, "none"]);

  for (let b = 0; b < BRANCH_COUNT; b++) {
    const branchId = `branch-${b}`;
    branchIds.push(branchId);
    rows.push([branchId, SYNTHETIC_ROOT, `Branch ${b}`, "level", 0, "none"]);
    for (let g = 0; g < GROUPS_PER_BRANCH; g++) {
      const groupId = `${branchId}-group-${g}`;
      rows.push([groupId, branchId, `Group ${b}.${g}`, "frame", 0, "none"]);
      for (let l = 0; l < LEAVES_PER_GROUP; l++) {
        const leafId = `${groupId}-leaf-${l}`;
        rows.push([leafId, groupId, `Member ${b}.${g}.${l}`, "member", 1, "mesh"]);
        // One sample leaf per group keeps the sample spread across every
        // branch without walking the whole array afterwards.
        if (l === 0) sampleLeafIds.push(leafId);
      }
    }
  }

  return {
    wire: {
      schema: HIERARCHY_SCHEMA,
      provider: "fixture-lines",
      collection: "plant-a",
      root: SYNTHETIC_ROOT,
      produced_at: "2026-08-25T13:55:53Z",
      depth: 3,
      cols,
      rows,
    },
    branchIds,
    sampleLeafIds,
    rowCount: rows.length,
  };
}
