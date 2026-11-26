// The pure decision behind `useSelectionSnapshot` (useSelectionSnapshot.ts): given a plain bag of
// the raw store values, which SelectionSnapshot to hand the providers. Kept apart from the hook
// that gathers those values so this logic -- "builder" never falls through to a scene/mesh pick,
// "scene" never shows a builder/ref pick, the "none"-or-active-structure fallback -- is callable
// (and testable) with no React, no store subscriptions.

import type {BuilderCell, BuilderSelection} from "@/state/cellbuilder/types";
import type {BuilderRef} from "@/state/cellbuilder/refsSlice";
import type {CellGroup} from "@/utils/cellbuilder/groups";
import type {SelectionSnapshot} from "@/plugins/propertyProviders";
import {selectedRef, selectionName, selectionPlace} from "@/utils/cellbuilder/boxTarget";
import {isMirroredWindow} from "@/layout/sceneActions";
import {savedGroupOfSelection} from "@/utils/groups/groupScene";
import {selectionNodes} from "@/utils/groups/selectionNodes";

/** Every raw value `snapshotFrom` needs, gathered by the hook from whichever stores hold it. */
export interface SelectionSnapshotInputs {
  /** The picked element's name (objectInfoStore), "" when nothing is picked. */
  meshPickName: string;
  /** Whether the scene has a loaded model at all (treeViewStore). */
  treeLoaded: boolean;
  cbActive: boolean;
  cbSelection: BuilderSelection | null;
  cbSelectedIds: readonly string[];
  cbSelectedRefIds: readonly string[];
  cbCells: Readonly<Record<string, BuilderCell>>;
  cbRefs: Readonly<Record<string, BuilderRef>>;
  cbGroups: readonly CellGroup[];
  cbLoftMembers: readonly {NAME: string; STRUCTURE_NAME?: string}[];
  cbActiveGroup: string | null;
  /** Selected draw ranges: this window's own count, or the main window's when mirrored. */
  rangeCount: number;
  /** Whether a builder pick is this panel's to show now (`useBuilderPickShown`). */
  builderShown: boolean;
  /** The composed shell's active mode, if any (`activeShellMode`). */
  shellMode?: string;
}

/** The selection's snapshot for the providers' `match`; cell context wins, as in the summary.
 *  `selectionSource`: "builder" never falls through to a scene/mesh pick (so a stale GLB pick from
 *  another mode cannot show up here); "scene" never shows a builder/ref pick. Absent or "any":
 *  today's behaviour. */
export function snapshotFrom(
  inputs: SelectionSnapshotInputs,
  selectionSource: "builder" | "scene" | "any" = "any",
): SelectionSnapshot {
  const {
    meshPickName,
    treeLoaded,
    cbActive,
    cbSelection,
    cbSelectedIds,
    cbSelectedRefIds,
    cbCells,
    cbRefs,
    cbGroups,
    cbLoftMembers,
    cbActiveGroup,
    rangeCount,
    builderShown,
    shellMode,
  } = inputs;
  const base = {
    meshName: meshPickName || null,
    hasEntities: treeLoaded || (cbActive && Object.keys(cbCells).length > 0),
    cellBuilderActive: cbActive,
    selectionSource,
    ...(shellMode ? {shellMode} : {}),
  };
  if (selectionSource !== "scene" && cbActive && cbSelection !== null && builderShown) {
    // Where the pick is, so a plugin's section can place itself (and the builder's space and
    // structure sections know what to show).
    const place = selectionPlace(cbSelection, {cells: cbCells, refs: cbRefs, groups: cbGroups, loftMembers: cbLoftMembers});
    const name = selectionName(cbSelection, cbCells, cbRefs);
    const ref = selectedRef(cbSelection, cbRefs);
    // A mixed multi-select (a cell and a ref together) covers both sets in the count, whichever
    // one the primary happens to be.
    const count = cbSelectedIds.length + cbSelectedRefIds.length;
    if (ref) return {...base, ...place, kind: "ref", name, refOwner: ref.owner, refKey: ref.key, count};
    return {...base, ...place, kind: "cell", name, count};
  }
  // "builder": never fall through to a scene/mesh pick, however stale -- it belongs to whatever
  // mode last made it, not to this one. Straight to the shared "none"-or-structure fallback below.
  if (selectionSource !== "builder" && (rangeCount > 0 || meshPickName)) {
    // What is selected as element-link nodes (the Results section, a plugin's links); not in a
    // pop-out window, which has no 3D selection of its own.
    const nodes = isMirroredWindow() ? undefined : selectionNodes();
    const groupId = savedGroupOfSelection(meshPickName || null);
    return {
      ...base,
      kind: "mesh",
      name: meshPickName || null,
      count: rangeCount,
      ...(nodes ? {nodes} : {}),
      ...(groupId ? {group_id: groupId} : {}),
    };
  }
  // Nothing picked in an open model: the active structure still has its section.
  const structure = cbActive && cbActiveGroup && cbGroups.some((g) => g.name === cbActiveGroup) ? cbActiveGroup : undefined;
  return {...base, kind: "none", name: null, count: 0, ...(structure ? {structure} : {})};
}
