// Core's own commands, registered for every composed shell (`./ComposedLayout`)
// so its menu bar and command palette can name them (`CORE_COMMAND_IDS`). Each
// wraps an action core already has -- the viewer's keys (Shift+H, Shift+A,
// Ctrl+Z, ...), the Options panel's toggles, the layout's workspaces -- and adds
// no behaviour of its own. Chords core already handles are shown with
// `shortcut`, which names the registry entry (`./coreShortcutTable`) that binds the key, so the\n// chord shown is the real one.
//
// The scene-side actions are imported when run, so registering the commands
// costs no scene code.

import {useMemo} from "react";
import {useStore} from "zustand";

import {runtime} from "@/runtime/config";
import {listUiShells, activeUiShellId, setActiveUiShell} from "@/plugins/uiShells";
import {promptDialog} from "@/plugins/dialogs";
import {useCellBuilderStore} from "@/state/cellBuilderStore";
import {useGalleryStore} from "@/state/galleryStore";
import {useObjectInfoStore} from "@/state/objectInfoStore";
import {useOptionsStore} from "@/state/optionsStore";
import {requestRender} from "@/state/perfStore";
import {THEME_PRESETS, useThemeStore, type ThemePresetId} from "@/state/themeStore";
import {useSelectedObjectStore} from "@/state/useSelectedObjectStore";
import {useViewerHistoryStore} from "@/state/viewerHistoryStore";
import {getViewerRuntime} from "@/state/viewerRuntime";

import {CORE_COMMAND_IDS as ID, themeCommandId, uiShellCommandId, workspaceCommandId} from "./coreCommandIds";
import {registerCommand, unregisterCommand, type CommandSpec} from "./commands";
import {layoutStore} from "./layoutStore";
import {deleteWorkspace, listWorkspaces, loadWorkspace, resetArrangement, saveWorkspace} from "./presets/classicPlace";
import {openNewWindow, thisWindowId, windowBus} from "./windows";
import {MAIN_WINDOW_ID} from "./windowBus";

const OWNER = "core";

/** How many draw ranges are selected. */
export function selectionCount(selected: ReadonlyMap<unknown, ReadonlySet<string>> = useSelectedObjectStore.getState().selectedObjects): number {
  let n = 0;
  for (const ids of selected.values()) n += ids.size;
  return n;
}

const nothingSelected = () => (selectionCount() === 0 ? "Nothing selected" : null);

/** The procedural builder takes Ctrl+Z / Ctrl+Y for its own history while it is open. */
const builderOwnsHistory = () => (useCellBuilderStore.getState().active !== null ? "The procedural builder has its own undo" : null);

/** The scene's handles, when a canvas is mounted. */
function sceneHandles() {
  const {scene, camera, controls} = getViewerRuntime();
  return scene.current && camera.current && controls.current ? {scene: scene.current, camera: camera.current, controls: controls.current} : null;
}

const noScene = () => (sceneHandles() ? null : "No 3D view");

const fullscreenAvailable = () => typeof document !== "undefined" && !!document.documentElement?.requestFullscreen;
const isFullscreen = () => typeof document !== "undefined" && !!document.fullscreenElement;

/** Core's commands, as specs (registered by `registerCoreCommands`). */
export function coreCommandSpecs(): CommandSpec[] {
  const camera = () => import("@/components/viewer/sceneHelpers/setupCameraControlsHandlers");
  const visibility = () => import("@/utils/scene/visibility");
  const specs: CommandSpec[] = [
    // File
    {
      id: ID.uploadFile,
      label: "Upload file…",
      group: "File",
      when: () => runtime.isRestMode(),
      run: () => void import("@/utils/scene/handlers/upload_source_file").then((m) => m.triggerUploadPicker()),
    },
    // Edit
    {
      id: ID.undo,
      label: "Undo",
      group: "Edit",
      shortcut: "core:undo",
      disabledReason: () => builderOwnsHistory() ?? (useViewerHistoryStore.getState().past.length ? null : "Nothing to undo"),
      run: () => void import("@/utils/scene/viewerHistory").then((m) => m.undoViewerAction()),
    },
    {
      id: ID.redo,
      label: "Redo",
      group: "Edit",
      shortcut: "core:redo",
      disabledReason: () => builderOwnsHistory() ?? (useViewerHistoryStore.getState().future.length ? null : "Nothing to redo"),
      run: () => void import("@/utils/scene/viewerHistory").then((m) => m.redoViewerAction()),
    },
    {
      id: ID.clearSelection,
      label: "Clear selection",
      group: "Edit",
      disabledReason: nothingSelected,
      // What clicking empty space in the tree does: one undo entry, the info panel emptied.
      run: () =>
        void import("@/utils/scene/viewerHistory").then((m) => {
          m.recordViewerAction("clear-selection", () => useSelectedObjectStore.getState().clearSelectedObjects());
          useObjectInfoStore.getState().setName(null);
          useObjectInfoStore.getState().setSelectedNodeId(null);
          requestRender();
        }),
    },
    {
      id: ID.copyNames,
      label: "Copy names",
      group: "Edit",
      shortcut: "core:copy-names",
      disabledReason: nothingSelected,
      run: () =>
        void import("@/utils/clipboard/copySelectionNames").then((m) => m.copySelectionNames(useSelectedObjectStore.getState().selectedObjects)),
    },
    {
      id: ID.groupSelection,
      label: "Save selection as group…",
      group: "Edit",
      shortcut: "core:group-selection",
      disabledReason: nothingSelected,
      run: () => void import("@/utils/groups/groupShortcut").then((m) => m.groupSelectionFromKeyboard()),
    },
    {
      id: ID.hideSelected,
      label: "Hide selected",
      group: "Edit",
      shortcut: "core:hide-selected",
      disabledReason: nothingSelected,
      run: () => void visibility().then((m) => m.hideSelectedRanges()),
    },
    {
      id: ID.hideOthers,
      label: "Hide others",
      group: "Edit",
      shortcut: "core:hide-others",
      disabledReason: nothingSelected,
      run: () => void visibility().then((m) => m.hideUnselectedRanges()),
    },
    {
      id: ID.showAll,
      label: "Show all",
      group: "Edit",
      shortcut: "core:show-all",
      run: () => void visibility().then((m) => m.unhideAllRanges()),
    },
    // View
    {
      id: ID.fitAll,
      label: "Fit all",
      group: "View",
      shortcut: "core:fit-all",
      disabledReason: noScene,
      run: () =>
        void camera().then((m) => {
          const h = sceneHandles();
          if (h) m.fitAllView(h.scene, h.camera, h.controls);
          requestRender();
        }),
    },
    {
      id: ID.fitSelection,
      label: "Fit selection",
      group: "View",
      shortcut: "core:fit-selection",
      disabledReason: () => noScene() ?? nothingSelected(),
      run: () =>
        void camera().then((m) => {
          const h = sceneHandles();
          if (h) m.fitSelectionView(h.camera, h.controls);
          requestRender();
        }),
    },
    {
      id: ID.resetCamera,
      label: "Reset camera",
      group: "View",
      disabledReason: noScene,
      run: () =>
        void camera().then((m) => {
          const h = sceneHandles();
          if (h) m.resetCameraHome(h.camera, h.controls);
        }),
    },
    {
      id: ID.edges,
      label: "Geometry edges",
      group: "View",
      checked: () => useOptionsStore.getState().showEdges,
      // As the Options panel's toggle: the overlays are rebuilt at once.
      run: () => {
        const o = useOptionsStore.getState();
        o.setShowEdges(!o.showEdges);
        void import("@/utils/scene/refreshEdgeOverlays").then((m) => m.refreshEdgeOverlays());
      },
    },
    {
      id: ID.stats,
      label: "Performance stats",
      group: "View",
      checked: () => useOptionsStore.getState().showPerf,
      run: () => {
        const o = useOptionsStore.getState();
        o.setShowPerf(!o.showPerf);
      },
    },
    {
      id: ID.gallery,
      label: "Gallery mode",
      group: "View",
      shortcut: "core:toggle-gallery",
      checked: () => useGalleryStore.getState().enabled,
      run: () => useGalleryStore.getState().toggle(),
    },
    {
      id: ID.fullscreen,
      label: "Full screen",
      group: "View",
      when: fullscreenAvailable,
      checked: isFullscreen,
      run: () => {
        if (isFullscreen()) void document.exitFullscreen?.();
        else void document.documentElement.requestFullscreen?.();
      },
    },
    // Window
    {
      id: ID.newWindow,
      label: "New window",
      group: "Window",
      // From the main window; a pop-out window opens its own from its Move menus.
      when: () => thisWindowId() === MAIN_WINDOW_ID,
      run: () => {
        if (!openNewWindow(windowBus())) console.warn("[windows] the browser blocked the new window");
      },
    },
    {id: ID.resetLayout, label: "Reset layout", group: "Window", run: () => resetArrangement()},
    {id: ID.resetAllLayouts, label: "Reset every mode's layout", group: "Window", run: () => resetArrangement(true)},
    {
      id: ID.saveWorkspace,
      label: "Save workspace…",
      group: "Window",
      run: () =>
        void promptDialog({title: "Save workspace", body: "The arrangement of every mode, under a name.", label: "Name", confirmLabel: "Save"}).then(
          (name) => {
            if (name?.trim()) saveWorkspace(name);
          },
        ),
    },
    {
      id: ID.deleteWorkspace,
      label: "Delete workspace…",
      group: "Window",
      when: () => listWorkspaces().length > 0,
      run: () =>
        void promptDialog({title: "Delete workspace", body: `Saved: ${listWorkspaces().join(", ")}`, tone: "danger", label: "Name", confirmLabel: "Delete"}).then(
          (name) => {
            const n = name?.trim();
            if (n && listWorkspaces().includes(n)) deleteWorkspace(n);
          },
        ),
    },
    // Help
    {
      id: ID.shortcuts,
      label: "Keyboard shortcuts",
      group: "Help",
      run: () => void import("@/components/options/ShortcutsModal").then((m) => m.openShortcutsDialog()),
    },
  ];
  for (const [id, preset] of Object.entries(THEME_PRESETS)) {
    specs.push({
      id: themeCommandId(id),
      label: preset.name,
      group: "Theme",
      checked: () => {
        const t = useThemeStore.getState();
        return t.preset === id && !t.customBg && !t.customText;
      },
      run: () => useThemeStore.getState().setPreset(id as ThemePresetId),
    });
  }
  for (const shell of listUiShells()) {
    specs.push({
      id: uiShellCommandId(shell.id),
      label: shell.label,
      group: "User interface",
      checked: () => activeUiShellId() === shell.id,
      // Persisted, then a reload: a shell owns the whole tree.
      run: () => {
        if (activeUiShellId() !== shell.id) setActiveUiShell(shell.id);
      },
    });
  }
  return specs;
}

/**
 * Run core command `id`'s action here, registered or not and without its
 * `when` / `disabledReason` (the caller decided): what a pop-out window's
 * mirrored panel asks the main window for (`./sceneActions`). False for an id
 * core has no command for.
 */
export function runCoreCommandAction(id: string): boolean {
  const spec = coreCommandSpecs().find((s) => s.id === id);
  if (!spec) return false;
  spec.run();
  return true;
}

let workspaceIds: string[] = [];

/** One "load" command per saved workspace, kept in step with the saved ones. */
function syncWorkspaceCommands(): void {
  const names = listWorkspaces();
  const ids = names.map(workspaceCommandId);
  for (const id of workspaceIds) if (!ids.includes(id)) unregisterCommand(id);
  names.forEach((name, i) => {
    if (workspaceIds.includes(ids[i])) return;
    registerCommand({id: ids[i], label: name, group: "Workspaces", run: () => void loadWorkspace(name)}, OWNER);
  });
  workspaceIds = ids;
}

let unsubscribe: (() => void) | null = null;

/** Register core's commands (idempotent; a later call re-registers them, e.g. after the shell list changed). */
export function registerCoreCommands(): void {
  for (const spec of coreCommandSpecs()) registerCommand(spec, OWNER);
  syncWorkspaceCommands();
  unsubscribe ??= layoutStore.subscribe((s, prev) => {
    if (s.prefs.workspaces !== prev.prefs.workspaces) syncWorkspaceCommands();
  });
}

/** The active shell's saved workspaces, re-rendering when they change (for a menu of them). */
export function useWorkspaceNames(): string[] {
  const key = useStore(layoutStore, () => listWorkspaces().join("\u0000"));
  return useMemo(() => (key ? key.split("\u0000") : []), [key]);
}
