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
  undo: "core:undo",
  redo: "core:redo",
  clearSelection: "core:clear-selection",
  copyNames: "core:copy-names",
  groupSelection: "core:group-selection",
  hideSelected: "core:hide-selected",
  hideOthers: "core:hide-others",
  showAll: "core:show-all",
  fitAll: "core:fit-all",
  fitSelection: "core:fit-selection",
  resetCamera: "core:reset-camera",
  edges: "core:edges",
  stats: "core:stats",
  gallery: "core:gallery",
  fullscreen: "core:fullscreen",
  shortcuts: "core:shortcuts",
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

/** The command that picks panel theme preset `id` (`slate`, `dark`, `mist`, `pale`). */
export const themeCommandId = (id: string): string => `core:theme.${id}`;

/** The command that switches to UI shell `id` (persisted, then a reload). */
export const uiShellCommandId = (id: string): string => `core:ui.${id}`;

/** The command that loads saved workspace `name`. */
export const workspaceCommandId = (name: string): string => `core:workspace.${name}`;
