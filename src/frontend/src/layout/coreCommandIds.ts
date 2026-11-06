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
