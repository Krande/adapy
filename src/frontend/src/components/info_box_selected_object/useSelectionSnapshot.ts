// The Selected Object panel's selection hooks: `useSelectionSnapshot` gathers every store value
// `snapshotFrom` (selectionSnapshot.ts) needs and memoizes the result; `useBuilderPickShown` and
// `useSelectedRangeCount` are its own two sub-hooks, each used by more than one caller
// (SelectionSummary reads both directly too).

import React, {useSyncExternalStore} from "react";
import type {SelectionSnapshot} from "@/plugins/propertyProviders";
import {useViewerStores} from "@/state/AdaViewerContext";
import {useCellBuilderStore} from "@/state/cellBuilderStore";
import {useSceneMirrorStore} from "@/state/sceneMirrorStore";
import {isMirroredWindow} from "@/layout/sceneActions";
import {activeShellMode, builderPresence, shellModesVersion, subscribeShellModes} from "@/layout/shellModes";
import {builderCellsShown} from "@/state/cellbuilder/builderVisibility";
import {onBeforeDraw} from "@/viewport/api/beforeDraw";
import {useSelectionCount} from "@/viewport/api/useViewportSelection";
import {snapshotFrom} from "./selectionSnapshot";

/** Whether a builder pick is the panel's now, by what the active mode declared (`builderPresence`). */
export function builderPickShownNow(): boolean {
  const presence = builderPresence();
  if (presence === "edit") return true;
  if (presence === "away") return false;
  // "compare": while the builder's cells are on screen (or no builder scene says otherwise).
  return builderCellsShown() !== false;
}

/**
 * Whether a builder pick is the panel's. The active mode says (`ModeBehaviour.builder`): where the
 * procedural model is edited ("edit", and core's own UI, which has no modes) it always is; in a mode
 * that keeps the model open behind it ("away") it is no selection there -- no summary, no section;
 * in a mode that draws the builder to compare ("compare") it is while the cells are drawn, followed
 * frame by frame as the builder's layer is hidden after a switch.
 */
export function useBuilderPickShown(): boolean {
  useSyncExternalStore(subscribeShellModes, shellModesVersion, shellModesVersion);
  const [, redraw] = React.useReducer((n: number) => n + 1, 0);
  const shown = builderPickShownNow();
  const seen = React.useRef(shown);
  seen.current = shown;
  React.useEffect(
    () =>
      onBeforeDraw(() => {
        if (builderPickShownNow() !== seen.current) redraw();
      }),
    [],
  );
  return shown;
}

/**
 * How many draw ranges are selected: counted here in the main window; in a
 * pop-out window, which has no 3D selection, the main window's count
 * (`core:selection`).
 */
export function useSelectedRangeCount(): number {
  // On the viewport on screen, whichever it is.
  const selected = useSelectionCount();
  const mirrored = useSceneMirrorStore((s) => s.rangeCount);
  return isMirroredWindow() ? mirrored : selected;
}

/** The selection's snapshot for the providers' `match` (see `snapshotFrom` for the decisions). */
export function useSelectionSnapshot(selectionSource: "builder" | "scene" | "any" = "any"): SelectionSnapshot {
  const {useObjectInfoStore, useSelectedObjectStore, useTreeViewStore} = useViewerStores();
  const name = useObjectInfoStore((s) => s.name);
  const selectedObjects = useSelectedObjectStore((s) => s.selectedObjects);
  const treeData = useTreeViewStore((s) => s.treeData);
  const cbActive = useCellBuilderStore((s) => s.active !== null);
  const cbSelection = useCellBuilderStore((s) => s.selection);
  const cbSelectedIds = useCellBuilderStore((s) => s.selectedCellIds);
  const cbSelectedRefIds = useCellBuilderStore((s) => s.selectedRefIds);
  const cbCells = useCellBuilderStore((s) => s.cells);
  const cbRefs = useCellBuilderStore((s) => s.refs);
  const cbGroups = useCellBuilderStore((s) => s.groups);
  const cbLoftMembers = useCellBuilderStore((s) => s.loftMembers);
  const cbActiveGroup = useCellBuilderStore((s) => s.activeGroup);
  const rangeCount = useSelectedRangeCount();
  const builderShown = useBuilderPickShown();
  // The shell's mode, so a section (or the shell's `sections`) can keep to the modes it is for.
  const shellMode = useSyncExternalStore(subscribeShellModes, activeShellMode, activeShellMode);
  return React.useMemo<SelectionSnapshot>(
    () =>
      snapshotFrom(
        {
          meshPickName: name ?? "",
          treeLoaded: treeData != null,
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
          shellMode: shellMode ?? undefined,
        },
        selectionSource,
      ),
    // eslint-disable-next-line react-hooks/exhaustive-deps -- selectedObjects isn't read by
    // snapshotFrom directly, but a selection change is reflected through rangeCount/name; kept
    // in the dep list as the original hook did, so a change to the raw store still redraws.
    [name, selectedObjects, treeData, cbActive, cbSelection, cbSelectedIds, cbSelectedRefIds, cbCells, cbRefs, cbGroups, cbLoftMembers, cbActiveGroup, rangeCount, shellMode, builderShown, selectionSource],
  );
}
