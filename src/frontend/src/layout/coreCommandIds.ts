// The ids of core's own commands (`./coreCommands`), apart from the commands
// themselves (which import the stores and the scene), so the dependency-free
// facade root can export them: a composed shell names them in its `MenuDef`s
// without loading them. Core registers the commands for every composed shell.
//
// Core's panels and modes have commands too, by pattern rather than by name:
// `panel:<panel id>` and `mode:<mode id>` (`./commands`).

export const CORE_COMMAND_IDS = {
  /** Pick a file to upload to storage (REST viewer only). */
  uploadFile: "core:upload-file",
  /** Pick GLB / glTF files from disk and open them in the scene, no upload (other formats go through storage on a converting server). */
  openFile: "core:open-file",
  /** The export dialog on the selection (STEP / IFC, written by the server; REST viewer). */
  exportSelection: "core:export-selection",
  /** The export dialog on a whole loaded model. */
  exportModel: "core:export-model",
  undo: "core:undo",
  redo: "core:redo",
  /** Select every shown element of the loaded models. */
  selectAll: "core:select-all",
  /** Select the shown elements that are not selected, deselect the rest. */
  invertSelection: "core:invert-selection",
  clearSelection: "core:clear-selection",
  copyNames: "core:copy-names",
  groupSelection: "core:group-selection",
  hideSelected: "core:hide-selected",
  hideOthers: "core:hide-others",
  showAll: "core:show-all",
  fitAll: "core:fit-all",
  fitSelection: "core:fit-selection",
  resetCamera: "core:reset-camera",
  /** Named camera views: the model framed from one side (`cameraViewCommandId`). */
  viewTop: "core:view.top",
  viewBottom: "core:view.bottom",
  viewFront: "core:view.front",
  viewBack: "core:view.back",
  viewLeft: "core:view.left",
  viewRight: "core:view.right",
  viewIsometric: "core:view.isometric",
  edges: "core:edges",
  stats: "core:stats",
  gallery: "core:gallery",
  fullscreen: "core:fullscreen",
  shortcuts: "core:shortcuts",
  /** The About dialog: the viewer's and the server's versions, links to the documentation. */
  about: "core:about",
  newWindow: "core:new-window",
  resetLayout: "core:reset-layout",
  resetAllLayouts: "core:reset-all-layouts",
  saveWorkspace: "core:save-workspace",
  deleteWorkspace: "core:delete-workspace",
  /** Opens the palette; registered when the shell has one (`UiShellChrome.commandPalette`). */
  commandPalette: "core:command-palette",
  /** Reopens the dashboard's closed windows (`REOPEN_WINDOWS_COMMAND`); offered while there are any. */
  reopenWindows: "core:reopen-windows",
  /** Isolate the loaded FEA result's element sets whose name holds a text (asked for). */
  feaIsolateSets: "core:fea-isolate-sets",
  /** Isolate the element sets the selected FEA elements belong to. */
  feaIsolateSelectionSets: "core:fea-isolate-selection-sets",
  /** Show the whole FEA model again. */
  feaClearIsolation: "core:fea-clear-isolation",
  /** Draw what an FEA isolation hides as a wireframe ("context"), or hide it outright. */
  feaToggleContextWireframe: "core:fea-toggle-context-wireframe",
  /** Open the marking menu at the pointer over the 3D view (Shift+E); offered where the UI has one turned on. */
  markingMenu: "core:marking-menu",
  /** Turn the marking menu on or off in this UI (checked while on); offered where the UI has one. */
  toggleMarkingMenu: "core:toggle-marking-menu",
  /** The tool rail's customise dialog; offered in a shell with core's tool rail. */
  customiseRail: "core:customise-rail",
  /** Result playback (a streaming FEA result): play / pause, stop, a step back and forward. */
  playPause: "core:results-play-pause",
  stopResults: "core:results-stop",
  stepBack: "core:results-step-back",
  stepForward: "core:results-step-forward",
  /** Section planes: add one on an axis (it becomes the one the toolbar edits), switch it on or off, flip it, remove all. */
  sectionX: "core:section.x",
  sectionY: "core:section.y",
  sectionZ: "core:section.z",
  sectionToggle: "core:section-toggle",
  sectionFlip: "core:section-flip",
  sectionClear: "core:section-clear",
  /** Representation of a loaded FEA result: the undeformed outline, the element edges, the result colours. */
  feaToggleUndeformed: "core:fea-toggle-undeformed",
  feaToggleElementEdges: "core:fea-toggle-element-edges",
  feaToggleResultColors: "core:fea-toggle-result-colors",
  /** Number labels over a loaded FEA result: node ids, element ids (all in view, or those of the selection). */
  labelsNodes: "core:labels-nodes",
  labelsElements: "core:labels-elements",
  /** Colour by property: the next source (an FEA property, the models, a registered one), and back to the ordinary colours. */
  colorByNext: "core:color-by-next",
  colorByModel: "core:color-by-model",
  colorByReset: "core:color-by-reset",
  /** The procedural builder: its own undo and redo, compile (server, forced, in the browser), commit, close the model. */
  builderUndo: "core:builder-undo",
  builderRedo: "core:builder-redo",
  builderCompile: "core:builder-compile",
  builderRecompile: "core:builder-recompile",
  builderCompileBrowser: "core:builder-compile-browser",
  builderCommit: "core:builder-commit",
  builderClose: "core:builder-close",
} as const;

export type CoreCommandId = (typeof CORE_COMMAND_IDS)[keyof typeof CORE_COMMAND_IDS];

/** The named camera views, in menu order. */
export const CAMERA_VIEWS = ["top", "bottom", "front", "back", "left", "right", "isometric"] as const;
export type CameraView = (typeof CAMERA_VIEWS)[number];

/** The command that frames the model from view `view` (`core:view.top`, ...). */
export const cameraViewCommandId = (view: CameraView): string => `core:view.${view}`;

/** The command that picks panel theme preset `id` (`slate`, `dark`, `mist`, `pale`). */
export const themeCommandId = (id: string): string => `core:theme.${id}`;

/** The command that switches to UI shell `id` (persisted, then a reload). */
export const uiShellCommandId = (id: string): string => `core:ui.${id}`;

/** The command that loads saved workspace `name`. */
export const workspaceCommandId = (name: string): string => `core:workspace.${name}`;
