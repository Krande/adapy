// Core's own commands, registered for every composed shell (`./ComposedLayout`)
// so its menu bar and command palette can name them (`CORE_COMMAND_IDS`). Each
// wraps an action core already has -- the viewer's keys (Shift+H, Shift+A,
// Ctrl+Z, ...), the Options panel's toggles, the layout's workspaces -- or one
// of the scene actions made for the menus (open a local file, select all /
// invert, the named camera views, the export and About dialogs). Chords core
// handles are shown with `shortcut`, which names the registry entry
// (`./coreShortcutTable`) that binds the key, so the chord shown is the real one.
//
// The scene-side actions are imported when run, so registering the commands
// costs no scene code.

import {useMemo} from "react";
import {useStore} from "zustand";

import {runtime} from "@/runtime/config";
import {listUiShells, activeUiShellId, setActiveUiShell} from "@/plugins/uiShells";
import {promptDialog} from "@/plugins/dialogs";
import {capabilities} from "@/services/capabilities";
import {useCellBuilderStore} from "@/state/cellBuilderStore";
import {useFeaAnimationStore} from "@/state/feaAnimationStore";
import {useGalleryStore} from "@/state/galleryStore";
import {useSectionStore, type SectionPlane} from "@/state/sectionStore";
import {useObjectInfoStore} from "@/state/objectInfoStore";
import {useOptionsStore} from "@/state/optionsStore";
import {requestRender} from "@/state/perfStore";
import {THEME_PRESETS, useThemeStore, type ThemePresetId} from "@/state/themeStore";
import {useSelectedObjectStore} from "@/state/useSelectedObjectStore";
import {useViewerHistoryStore} from "@/state/viewerHistoryStore";
import {getViewerRuntime} from "@/state/viewerRuntime";
import {loadedSourceGroups, useModelState} from "@/state/modelState";
import {useColorByStore} from "@/state/colorByStore";
import {useColorStore} from "@/state/colorLegendStore";
import {useFemConceptsStore} from "@/state/femConceptsStore";
import {useTableNavStore} from "@/state/tableNavStore";
import {feModelLoaded, hasFemConcepts} from "@/components/simulation/simPanelTabs";
import {beamSolidsToggleState} from "@/utils/scene/fea/streaming/beamSolidsToggle";
import {feaControlActions} from "@/utils/scene/fea/feaControls";
import {useLabelsStore} from "@/state/labelsStore";
import {anyShownRange} from "@/utils/mesh_select/selectionSets";
import {playbackAvailable} from "@/utils/scene/fea/playbackActions";
import {openLocalFilePicker} from "@/utils/scene/handlers/localOpenPlan";
import {feaContextReason, feaIsolationNow, feaIsolationReason, feaTreeView} from "@/utils/scene/fea/feaTree";

import {
  CAMERA_VIEWS,
  CORE_COMMAND_IDS as ID,
  cameraViewCommandId,
  themeCommandId,
  uiShellCommandId,
  workspaceCommandId,
  type CameraView,
} from "./coreCommandIds";
import {openCoreDialog} from "./coreDialogs";
import {registerCommand, unregisterCommand, type CommandSpec} from "./commands";
import {layoutStore} from "./layoutStore";
import {listShellModes} from "./shellModes";
import {registerOptionCommands} from "./optionCommands";
import {setUiFrozen, uiFreezeOffered, uiFrozen} from "./uiFreeze";
import {builderLoaded} from "./modeRequirements";
import {deleteWorkspace, listWorkspaces, loadWorkspace, resetArrangement, saveWorkspace} from "./presets/classicPlace";
import {openNewWindow, thisWindowId, windowBus} from "./windows";
import {MAIN_WINDOW_ID} from "./windowBus";
import {sceneActions} from "./sceneActions";
import {CORE_COMMAND_ICONS} from "./commandIcons";
import {openFemConcepts, toggleColorLegend, toggleDataTable} from "./resultViewActions";
import {markingMenuMounted, openMarkingMenu, servedMarkingMenu, setMarkingMenuOn} from "./markingMenuState";
import {activeRail, openRailCustomise} from "./toolRailState";
import {FEA_ELEMENT_SETS_ID, FEA_SETS_ROOT_ID, elementSetFromRowId} from "@/utils/scene/fea/feaTreeRows";

/** Element sets the FEA rows list (here, or the main window's in a pop-out). */
function feaSetCount(): number {
  const sets = feaTreeView().roots.find((r) => r.id === FEA_SETS_ROOT_ID);
  if (!sets) return 0;
  const branch = sets.children.find((c) => c.id === FEA_ELEMENT_SETS_ID);
  return (branch ?? sets).children.filter((c) => elementSetFromRowId(c.id) !== null).length;
}

const OWNER = "core";

const noResults = () => (playbackAvailable() ? null : "No result is loaded");

/** The section plane the toolbar edits: the active one, else the last. */
export function workingPlane(): SectionPlane | undefined {
  const {planes, activeId} = useSectionStore.getState();
  return planes.find((p) => p.id === activeId) ?? planes[planes.length - 1];
}

type FeaState = ReturnType<typeof useFeaAnimationStore.getState>;

/** A switch of how a loaded FEA result is drawn: ticked while on, offered once a result is loaded. */
function feaViewToggle(
  id: string,
  label: string,
  read: (s: FeaState) => boolean,
  setter: (s: FeaState) => (v: boolean) => void,
): CommandSpec {
  return {
    id,
    label,
    group: "View",
    checked: () => read(useFeaAnimationStore.getState()),
    disabledReason: () => (useFeaAnimationStore.getState().sessionActive ? null : "No result is loaded"),
    run: () => {
      const s = useFeaAnimationStore.getState();
      setter(s)(!read(s));
      requestRender();
    },
  };
}
/** How many draw ranges are selected. */
export function selectionCount(selected: ReadonlyMap<unknown, ReadonlySet<string>> = useSelectedObjectStore.getState().selectedObjects): number {
  let n = 0;
  for (const ids of selected.values()) n += ids.size;
  return n;
}

const nothingSelected = () => (selectionCount() === 0 ? "Nothing selected" : null);

/** The procedural builder takes Ctrl+Z / Ctrl+Y for its own history while it is open. */
const builderOwnsHistory = () => (useCellBuilderStore.getState().active !== null ? "The procedural builder has its own undo" : null);

const noBuilderModel = () => (builderLoaded() ? null : "No procedural model is open");

type BuilderState = ReturnType<typeof useCellBuilderStore.getState>;
type GizmoKind = "translate" | "rotate" | "resize";

/** The kind of the selected builder component, or null with none. */
function selectedComponentKind(s: BuilderState): "cell" | "equipment" | "loft" | "opening" | null {
  return s.selection ? (s.cells[s.selection.cellId]?.kind ?? null) : null;
}

/** Which components a gizmo works on (the G / R / S keys): rotate is for equipment, resize for cells. */
const GIZMO_TARGETS: Readonly<Record<GizmoKind, {kinds: readonly string[]; need: string}>> = {
  translate: {kinds: ["cell", "equipment", "loft", "opening"], need: "Select a cell or equipment to move"},
  rotate: {kinds: ["equipment"], need: "Select equipment to rotate"},
  resize: {kinds: ["cell"], need: "Select a cell to resize"},
};

/** One builder gizmo: pressing it brings it up on the selection, pressing it again puts it away. */
function builderGizmo(id: string, label: string, kind: GizmoKind): CommandSpec {
  return {
    id,
    label,
    group: "Build",
    shortcut: id,
    checked: () => useCellBuilderStore.getState().gizmoMode === kind,
    disabledReason: () => {
      const s = useCellBuilderStore.getState();
      const target = GIZMO_TARGETS[kind];
      const k = selectedComponentKind(s);
      return builderReadOnly() ?? (k && target.kinds.includes(k) ? null : target.need);
    },
    run: () => {
      const s = useCellBuilderStore.getState();
      s.setGizmoMode(s.gizmoMode === kind ? "none" : kind);
    },
  };
}

/** One of the builder's three views of the model (topology, simulation, detail). */
function builderRep(id: string, label: string, mode: BuilderState["repMode"]): CommandSpec {
  return {
    id,
    label,
    group: "Build",
    checked: () => useCellBuilderStore.getState().repMode === mode,
    disabledReason: noBuilderModel,
    run: () => void useCellBuilderStore.getState().setRepMode(mode),
  };
}

/** The type placed by "add opening" / "add equipment": the one picked last, else the first on offer. */
function placementType(picked: string | null, types: readonly {slug: string}[]): string | null {
  return picked && types.some((t) => t.slug === picked) ? picked : (types[0]?.slug ?? null);
}
/** Editing, compiling and committing need a model opened from storage, on a connection that can edit it. */
const builderReadOnly = () =>
  noBuilderModel() ??
  (useCellBuilderStore.getState().active
    ? capabilities.procedural.canEdit
      ? null
      : "This connection cannot edit procedural models"
    : "This model was read off the scene, not opened from storage");
const builderCompileBusy = () => {
  const job = useCellBuilderStore.getState().compileJob;
  return job && (job.status === "queued" || job.status === "running") ? "A compile is running" : null;
};
const builderServerCompile = () =>
  builderReadOnly() ??
  builderCompileBusy() ??
  (capabilities.procedural.supports("previewModel") ? null : "Server-side compile is not available over this connection");

/** The scene's handles, when a canvas is mounted. */
function sceneHandles() {
  const {scene, camera, controls} = getViewerRuntime();
  return scene.current && camera.current && controls.current ? {scene: scene.current, camera: camera.current, controls: controls.current} : null;
}

const noScene = () => (sceneHandles() ? null : "No 3D view");

/** Select all / invert take the shown elements: none, and there is nothing to do. */
const nothingToSelect = () => noScene() ?? (anyShownRange(getViewerRuntime().scene.current) ? null : "Nothing to select");

/** The exports are written by the server from the model's source. */
const notServer = () => (runtime.isRestMode() ? null : "Exports are written by a server, and this viewer is not connected to one");

/** The named views as the menus word them. */
export const CAMERA_VIEW_LABELS: Readonly<Record<CameraView, string>> = {
  top: "Top",
  bottom: "Bottom",
  front: "Front",
  back: "Back",
  left: "Left",
  right: "Right",
  isometric: "Isometric",
};

const fullscreenAvailable = () => typeof document !== "undefined" && !!document.documentElement?.requestFullscreen;
const isFullscreen = () => typeof document !== "undefined" && !!document.fullscreenElement;

/** Core's commands, as specs (registered by `registerCoreCommands`). */
export function coreCommandSpecs(): CommandSpec[] {
  const camera = () => import("@/components/viewer/sceneHelpers/setupCameraControlsHandlers");
  const visibility = () => import("@/utils/scene/visibility");
  const selection = () => import("@/utils/scene/selectionCommands");
  const playback = () => import("@/utils/scene/fea/playbackActions");
  const colorBy = () => import("@/utils/scene/colorBy");
  const navigation = () => import("@/utils/tree_view/treeNavigation");
  const specs: CommandSpec[] = [
    // File
    {
      id: ID.uploadFile,
      label: "Upload file…",
      group: "File",
      when: () => runtime.isRestMode(),
      run: () => void import("@/utils/scene/handlers/upload_source_file").then((m) => m.triggerUploadPicker()),
    },
    {
      id: ID.openFile,
      label: "Open…",
      group: "File",
      shortcut: "core:open-file",
      disabledReason: noScene,
      // The picker opens synchronously, inside the click: a browser shows one only from a gesture.
      run: () => openLocalFilePicker(),
    },
    {
      id: ID.exportSelection,
      label: "Export selection…",
      group: "File",
      disabledReason: () => notServer() ?? nothingSelected(),
      run: () => openCoreDialog({kind: "export", scope: "selection"}),
    },
    {
      id: ID.exportModel,
      label: "Export model…",
      group: "File",
      disabledReason: () => notServer() ?? (useModelState.getState().loadedSourceNames.size ? null : "No model loaded"),
      run: () => openCoreDialog({kind: "export", scope: "model"}),
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
      id: ID.selectAll,
      label: "Select all",
      group: "Edit",
      shortcut: "core:select-all",
      disabledReason: nothingToSelect,
      run: () => void selection().then((m) => m.selectAllRanges()),
    },
    {
      id: ID.invertSelection,
      label: "Invert selection",
      group: "Edit",
      disabledReason: nothingToSelect,
      run: () => void selection().then((m) => m.invertSelection()),
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
    // Walk the scene tree from the selection (Shift+arrows; the tree's own level navigation).
    {
      id: ID.selectParent,
      label: "Select parent",
      group: "Edit",
      shortcut: "core:select-parent",
      disabledReason: nothingSelected,
      run: () => sceneActions().selectParent(),
    },
    {
      id: ID.selectChild,
      label: "Select first child",
      group: "Edit",
      shortcut: "core:select-child",
      disabledReason: nothingSelected,
      run: () => void navigation().then((m) => m.selectChildLevel()),
    },
    {
      id: ID.selectPrevSibling,
      label: "Select previous sibling",
      group: "Edit",
      shortcut: "core:select-prev-sibling",
      disabledReason: nothingSelected,
      run: () => void navigation().then((m) => m.selectSibling(-1)),
    },
    {
      id: ID.selectNextSibling,
      label: "Select next sibling",
      group: "Edit",
      shortcut: "core:select-next-sibling",
      disabledReason: nothingSelected,
      run: () => void navigation().then((m) => m.selectSibling(1)),
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
    ...CAMERA_VIEWS.map(
      (view): CommandSpec => ({
        id: cameraViewCommandId(view),
        label: `${CAMERA_VIEW_LABELS[view]} view`,
        group: "View",
        disabledReason: noScene,
        run: () =>
          void import("@/components/viewer/sceneHelpers/namedViews").then((m) => {
            const h = sceneHandles();
            if (h) m.applyNamedView(view, h.scene, h.camera, h.controls, useModelState.getState().zIsUp);
            requestRender();
          }),
      }),
    ),
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
    // FEA: the loaded result's sets and properties (`@/utils/scene/fea/feaTree`), through the
    // scene actions so a pop-out window's palette acts on the main window's scene.
    {
      id: ID.feaIsolateSets,
      label: "Isolate FEA sets…",
      group: "FEA",
      disabledReason: () => feaIsolationReason() ?? (feaSetCount() ? null : "The loaded result carries no element sets"),
      run: () =>
        void promptDialog({
          title: "Isolate FEA sets",
          body: "Every element set whose name holds this text is isolated, together.",
          label: "Set name",
          confirmLabel: "Isolate",
        }).then((text) => {
          if (text?.trim()) sceneActions().feaIsolateMatching(text.trim());
        }),
    },
    {
      id: ID.feaIsolateSelectionSets,
      label: "Isolate the picked element's FEA sets",
      group: "FEA",
      disabledReason: () => feaIsolationReason() ?? (feaTreeView().picked?.sets.length ? null : "Pick an element that belongs to a set"),
      run: () => {
        const rows = feaTreeView().picked?.sets.map((s) => s.rowId) ?? [];
        if (rows.length) sceneActions().feaIsolate(rows, "replace");
      },
    },
    {
      id: ID.feaClearIsolation,
      label: "Clear FEA isolation",
      group: "FEA",
      disabledReason: () => feaIsolationReason() ?? (feaIsolationNow().rows.length ? null : "Nothing is isolated"),
      run: () => sceneActions().feaClearIsolation(),
    },
    {
      id: ID.feaToggleContextWireframe,
      label: "Show the rest as wireframe",
      group: "FEA",
      checked: () => feaIsolationNow().contextWireframe && feaContextReason() === null,
      disabledReason: feaContextReason,
      run: () => sceneActions().feaToggleContext(),
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
    {
      id: ID.toggleUiFreeze,
      label: "Freeze layout",
      group: "Window",
      when: uiFreezeOffered,
      checked: () => uiFrozen(),
      run: () => setUiFrozen(!uiFrozen()),
    },
    {
      id: ID.resetAllLayouts,
      label: "Reset every mode's layout",
      group: "Window",
      // Core's own UI (and a shell without modes) has one layout: "Reset layout" is the one.
      disabledReason: () => (listShellModes().length ? null : "This user interface has no modes"),
      run: () => resetArrangement(true),
    },
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
    {id: ID.about, label: "About", group: "Help", run: () => openCoreDialog({kind: "about"})},
    // The marking menu (`./MarkingMenu`) and the tool rail (`./ToolRail`).
    {
      id: ID.markingMenu,
      label: "Marking menu",
      group: "View",
      shortcut: "core:marking-menu",
      // Where the UI has it on, in the main window (its host is mounted there).
      when: () => servedMarkingMenu().on && markingMenuMounted(),
      disabledReason: noScene,
      run: () => void openMarkingMenu(),
    },
    {
      id: ID.toggleMarkingMenu,
      label: "Marking menu on the right button",
      group: "View",
      when: () => servedMarkingMenu().available,
      checked: () => servedMarkingMenu().on,
      run: () => setMarkingMenuOn(!servedMarkingMenu().on),
    },
    {
      id: ID.customiseRail,
      label: "Customise tool rail…",
      group: "Window",
      when: () => activeRail()?.config.customisable === true,
      run: () => openRailCustomise(),
    },
    // Results playback (the transport's actions, shared with the Simulation panel).
    {
      id: ID.playPause,
      label: "Play / pause results",
      group: "Results",
      disabledReason: noResults,
      checked: () => useFeaAnimationStore.getState().isPlaying,
      run: () => void playback().then((m) => m.togglePlayResults()),
    },
    {
      id: ID.stopResults,
      label: "Stop results",
      group: "Results",
      disabledReason: noResults,
      run: () => void playback().then((m) => m.stopResults()),
    },
    {
      id: ID.stepBack,
      label: "Previous result step",
      group: "Results",
      disabledReason: noResults,
      run: () => void playback().then((m) => m.stepResults(-1)),
    },
    {
      id: ID.stepForward,
      label: "Next result step",
      group: "Results",
      disabledReason: noResults,
      run: () => void playback().then((m) => m.stepResults(1)),
    },
    // Section planes
    ...(["x", "y", "z"] as const).map(
      (axis): CommandSpec => ({
        id: ID[`section${axis.toUpperCase() as "X" | "Y" | "Z"}`],
        label: `Section plane ${axis.toUpperCase()}`,
        group: "Section",
        run: () => useSectionStore.getState().addPlane(axis),
      }),
    ),
    {
      id: ID.sectionToggle,
      label: "Section plane on / off",
      group: "Section",
      checked: () => workingPlane()?.enabled === true,
      // With no plane yet, the first press cuts through the model on Z.
      run: () => {
        const s = useSectionStore.getState();
        const p = workingPlane();
        if (p) s.toggle(p.id);
        else s.addPlane("z");
      },
    },
    {
      id: ID.sectionFlip,
      label: "Flip section plane",
      group: "Section",
      disabledReason: () => (workingPlane() ? null : "No section plane"),
      run: () => {
        const p = workingPlane();
        if (p) useSectionStore.getState().flip(p.id);
      },
    },
    {
      id: ID.sectionClear,
      label: "Remove section planes",
      group: "Section",
      disabledReason: () => (useSectionStore.getState().planes.length ? null : "No section plane"),
      run: () => useSectionStore.getState().clearAll(),
    },
    // Representation of a loaded FEA result
    feaViewToggle(ID.feaToggleUndeformed, "Undeformed outline", (s) => s.showUndeformed, (s) => s.setShowUndeformed),
    feaViewToggle(ID.feaToggleElementEdges, "Element edges", (s) => s.elementEdgesVisible, (s) => s.setElementEdgesVisible),
    feaViewToggle(ID.feaToggleResultColors, "Result colours", (s) => s.resultColorsVisible, (s) => s.setResultColorsVisible),
    // The loaded result's view: the same state and actions as the Simulation panel's controls.
    {
      id: ID.colorLegend,
      label: "Colour legend",
      group: "Results",
      checked: () => useColorStore.getState().showLegend,
      disabledReason: () =>
        useFeaAnimationStore.getState().sessionActive || useColorByStore.getState().activeId !== null ? null : "No result is loaded",
      run: () => toggleColorLegend(),
    },
    {
      id: ID.femConcepts,
      label: "FE model (FEM concepts)",
      group: "Results",
      disabledReason: () =>
        feModelLoaded({manifest: useFeaAnimationStore.getState().manifest, femHasConcepts: hasFemConcepts(useFemConceptsStore.getState())})
          ? null
          : "No FE model is loaded",
      run: () => openFemConcepts(),
    },
    {
      id: ID.dataDock,
      label: "Result data table",
      group: "Results",
      checked: () => useTableNavStore.getState().isPanelOpen,
      disabledReason: () => (useFeaAnimationStore.getState().sessionActive ? null : "No result is loaded"),
      run: () => toggleDataTable(),
    },
    {
      id: ID.beamSections,
      label: "Beams as sections",
      group: "Results",
      checked: () => {
        const s = useFeaAnimationStore.getState();
        return beamSolidsToggleState(s.manifest, s.beamSolidsUnavailable).enabled && s.beamSolidsVisible;
      },
      disabledReason: () => {
        const s = useFeaAnimationStore.getState();
        if (!s.sessionActive) return "No result is loaded";
        const t = beamSolidsToggleState(s.manifest, s.beamSolidsUnavailable);
        return t.enabled ? null : t.offered ? t.title : "This result has no beam sections";
      },
      run: () => feaControlActions().setBeamSolids(!useFeaAnimationStore.getState().beamSolidsVisible),
    },
    {
      id: ID.warp,
      label: "Warp by displacement",
      group: "Results",
      checked: () => useFeaAnimationStore.getState().warpEnabled,
      disabledReason: () => (useFeaAnimationStore.getState().sessionActive ? null : "No result is loaded"),
      run: () => feaControlActions().setWarp(!useFeaAnimationStore.getState().warpEnabled),
    },
    // Number labels (node and element ids of a loaded FEA result)
    {
      id: ID.labelsNodes,
      label: "Node numbers",
      group: "View",
      checked: () => useLabelsStore.getState().nodes,
      disabledReason: () => (useFeaAnimationStore.getState().sessionActive ? null : "No result is loaded"),
      run: () => useLabelsStore.getState().setNodes(!useLabelsStore.getState().nodes),
    },
    {
      id: ID.labelsElements,
      label: "Element numbers",
      group: "View",
      checked: () => useLabelsStore.getState().elements,
      disabledReason: () => (useFeaAnimationStore.getState().sessionActive ? null : "No result is loaded"),
      run: () => useLabelsStore.getState().setElements(!useLabelsStore.getState().elements),
    },
    {
      id: ID.labelsValues,
      label: "Property values",
      group: "View",
      checked: () => useLabelsStore.getState().values,
      disabledReason: () => (useFeaAnimationStore.getState().sessionActive ? null : "No result is loaded"),
      run: () => useLabelsStore.getState().setValues(!useLabelsStore.getState().values),
    },
    // Colour by property
    {
      id: ID.colorByNext,
      label: "Colour by next property",
      group: "View",
      checked: () => useColorByStore.getState().activeId !== null,
      run: () =>
        void colorBy().then(async (m) => {
          const next = m.nextColorBySource();
          if (next) await m.applyColorBy(next);
        }),
    },
    {
      id: ID.colorByModel,
      label: "Colour by model",
      group: "View",
      checked: () => useColorByStore.getState().activeId === "model",
      disabledReason: () => (loadedSourceGroups.size > 1 ? null : "Needs two or more loaded models"),
      run: () =>
        void colorBy().then((m) => (useColorByStore.getState().activeId === "model" ? m.resetColorBy() : m.applyColorBy("model"))),
    },
    {
      id: ID.colorByReset,
      label: "Reset colours",
      group: "View",
      disabledReason: () => (useColorByStore.getState().activeId ? null : "Nothing is coloured by property"),
      run: () => void colorBy().then((m) => m.resetColorBy()),
    },
    // The procedural builder (the Build mode's toolbar)
    {
      id: ID.builderNew,
      label: "New procedural model…",
      group: "Build",
      disabledReason: () => (capabilities.procedural.canEdit ? null : "This connection cannot edit procedural models"),
      run: () => void import("@/utils/cellbuilder/newModel").then((m) => m.createAndOpenProceduralModel()),
    },
    {
      id: ID.builderUndo,
      label: "Undo build step",
      group: "Build",
      disabledReason: () => builderReadOnly() ?? (useCellBuilderStore.getState().past.length ? null : "Nothing to undo"),
      run: () => useCellBuilderStore.getState().undo(),
    },
    {
      id: ID.builderRedo,
      label: "Redo build step",
      group: "Build",
      disabledReason: () => builderReadOnly() ?? (useCellBuilderStore.getState().future.length ? null : "Nothing to redo"),
      run: () => useCellBuilderStore.getState().redo(),
    },
    {
      id: ID.builderCompile,
      label: "Compile preview",
      group: "Build",
      disabledReason: builderServerCompile,
      run: () => void useCellBuilderStore.getState().compilePreviewSelected(),
    },
    {
      id: ID.builderRecompile,
      label: "Recompile preview (force)",
      group: "Build",
      disabledReason: builderServerCompile,
      run: () => void useCellBuilderStore.getState().compilePreviewSelected(true),
    },
    {
      id: ID.builderCompileBrowser,
      label: "Compile in browser (WASM)",
      group: "Build",
      disabledReason: () => builderReadOnly() ?? builderCompileBusy(),
      run: () => void useCellBuilderStore.getState().compileInBrowser(),
    },
    {
      id: ID.builderCommit,
      label: "Commit model",
      group: "Build",
      disabledReason: () => {
        const s = useCellBuilderStore.getState();
        return builderReadOnly() ?? (s.committing ? "A commit is running" : s.dirty ? null : "No unsaved changes");
      },
      run: () => void useCellBuilderStore.getState().commit(),
    },
    {
      id: ID.builderClose,
      label: "Close procedural model",
      group: "Build",
      disabledReason: () => (useCellBuilderStore.getState().active ? null : "No procedural model is open"),
      run: () => useCellBuilderStore.getState().close(),
    },
    // Placement: the tools that put a component in the model (a second press puts the tool away).
    {
      id: ID.builderAddCell,
      label: "Add cell",
      group: "Build",
      checked: () => useCellBuilderStore.getState().mode === "add-cell",
      disabledReason: builderReadOnly,
      run: () => {
        const s = useCellBuilderStore.getState();
        s.setMode(s.mode === "add-cell" ? "idle" : "add-cell");
      },
    },
    {
      id: ID.builderAddLoft,
      label: "Add loft",
      group: "Build",
      disabledReason: builderReadOnly,
      run: () => useCellBuilderStore.getState().addLoftMember(),
    },
    {
      id: ID.builderAddOpening,
      label: "Add opening",
      group: "Build",
      checked: () => useCellBuilderStore.getState().mode === "add-opening",
      disabledReason: () => builderReadOnly() ?? (useCellBuilderStore.getState().openingTypes.length ? null : "No opening types"),
      run: () => {
        const s = useCellBuilderStore.getState();
        if (s.mode === "add-opening") return s.setMode("idle");
        const slug = placementType(s.selectedOpeningType, s.openingTypes);
        if (slug) {
          s.setSelectedOpeningType(slug);
          s.setMode("add-opening");
        }
      },
    },
    {
      id: ID.builderAddEquipment,
      label: "Add equipment",
      group: "Build",
      checked: () => useCellBuilderStore.getState().mode === "add-equipment",
      disabledReason: () => builderReadOnly() ?? (useCellBuilderStore.getState().equipmentTypes.length ? null : "No equipment types"),
      run: () => {
        const s = useCellBuilderStore.getState();
        if (s.mode === "add-equipment") return s.setMode("idle");
        const slug = placementType(s.selectedEquipmentType, s.equipmentTypes);
        if (slug) {
          s.setSelectedEquipmentType(slug);
          s.setMode("add-equipment");
        }
      },
    },
    // Gizmos on the selected component, as the G / R / S keys.
    builderGizmo(ID.builderTranslate, "Move gizmo", "translate"),
    builderGizmo(ID.builderRotate, "Rotate gizmo", "rotate"),
    builderGizmo(ID.builderResize, "Resize gizmo", "resize"),
    // What the builder shows.
    builderRep(ID.builderRepTopology, "Topology view", "topology"),
    builderRep(ID.builderRepSimulation, "Simulation view", "simulation"),
    builderRep(ID.builderRepDetail, "Detail view", "detail"),
    {
      id: ID.builderSuperimpose,
      label: "Result over topology",
      group: "Build",
      checked: () => useCellBuilderStore.getState().superimpose,
      disabledReason: () =>
        noBuilderModel() ?? (useCellBuilderStore.getState().repMode === "topology" ? "Needs the simulation or the detail view" : null),
      run: () => void useCellBuilderStore.getState().setSuperimpose(!useCellBuilderStore.getState().superimpose),
    },
    {
      id: ID.builderSideBySide,
      label: "Result beside topology",
      group: "Build",
      checked: () => useCellBuilderStore.getState().sideBySide,
      disabledReason: noBuilderModel,
      run: () => useCellBuilderStore.getState().setSideBySide(!useCellBuilderStore.getState().sideBySide),
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
      // Persisted, then in place (core's own UI, composed shells) or by a reload (`setActiveUiShell`).
      run: () => {
        if (activeUiShellId() !== shell.id) setActiveUiShell(shell.id);
      },
    });
  }
  // Icons, for the tool rail and the marking menu.
  for (const s of specs) s.icon ??= CORE_COMMAND_ICONS[s.id];
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
  registerOptionCommands();
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
