// Core's own keyboard shortcuts, as data: the one list the shortcuts registry
// (`./shortcuts`) reserves, the shortcuts dialog draws and a command's menu chord
// is read from. Pure data (no imports but a type), so the registry can hold it
// without pulling any scene code in.
//
// Two kinds of entry:
//   * dispatched -- the registry's listener runs them, once a viewer has bound
//     its handlers (`bindCoreShortcuts`, in `setupCameraControlsHandlers`).
//   * local -- a key a widget handles while it has focus or its mode is open
//     (the gallery strip, the procedural cell builder, the trees' Esc, the
//     F6 zone cycle). Listed and reserved here so the dialog and the conflict
//     check know them, but never dispatched: the widget keeps the key because it
//     needs its own state (a numeric entry in progress, which tree has focus, ...).

import type { ShortcutKeys } from "./shortcuts";

export interface CoreShortcutDef {
  /** `core:<name>`. */
  id: string;
  keys: ShortcutKeys;
  /** What it does, as the shortcuts dialog words it. */
  label: string;
  /** The dialog's group (see `CORE_SHORTCUT_GROUPS`). */
  group: string;
  /** Handled where the key's widget lives, not by the registry's listener. */
  local?: boolean;
  /** Whether plugins are refused this chord. Default: dispatched entries yes, local ones no. */
  reserves?: boolean;
  /** The browser's default is left alone (the key never had it prevented). Default: prevented once handled. */
  keepDefault?: boolean;
  /** Listed only while this holds (the dialog reads it). */
  requires?: "customisable-panels";
  /**
   * A local key that is one of a run (the digits that pick a cell type): every chord of it, `keys`
   * first. Listed as one line, its chord shown as "first–last".
   */
  range?: readonly ShortcutKeys[];
}

const VIEWER = "Global / viewer";
const CAMERA = "Camera";
const PANELS = "Panels";
const GALLERY = "Gallery walk";
const BUILDER = "Procedural cellbuilder";
const TYPED = "Procedural cellbuilder: typed values";

/** The digit keys `from` to `to`, as a `range`. */
const digits = (from: number, to: number): ShortcutKeys[] => Array.from({ length: to - from + 1 }, (_, i) => String(from + i));

/** The dialog's core groups, in order, with the line saying when their keys are live. */
export const CORE_SHORTCUT_GROUPS: ReadonlyArray<{ group: string; when: string }> = [
  { group: VIEWER, when: "Active whenever the viewer has focus (ignored while typing in a field)" },
  { group: CAMERA, when: "Active whenever the viewer has focus. Mouse and touch are set in Options → Camera controls" },
  { group: PANELS, when: "Active with Options → Theme → Customisable panels on" },
  { group: GALLERY, when: "Active while stepping through a files/geometry gallery" },
  { group: BUILDER, when: "Active when a procedural model's cell builder is open (these keys preempt the global ones)" },
  {
    group: TYPED,
    when: "While a value is being typed in the cell builder: an extrude (E), a loft resize (S), a placement (digits in add mode), an equipment insert or an opening (O)",
  },
];

export const CORE_SHORTCUTS: readonly CoreShortcutDef[] = [
  // Global / viewer -- dispatched
  { id: "core:hide-selected", keys: "Shift+H", label: "Hide the current selection", group: VIEWER, keepDefault: true },
  { id: "core:hide-others", keys: "Shift+I", label: "Hide everything except the current selection", group: VIEWER, keepDefault: true },
  { id: "core:group-selection", keys: "Shift+G", label: "Save the selection as a named group (shared in the scope)", group: VIEWER },
  { id: "core:show-all", keys: "Shift+U", label: "Unhide everything", group: VIEWER, keepDefault: true },
  {
    id: "core:show-selected",
    keys: "Shift+S",
    label: "Show the selection again (a level picked in the tree brings back everything hidden under it)",
    group: VIEWER,
    keepDefault: true,
  },
  {
    id: "core:undo",
    keys: "Ctrl+Z",
    label: "Undo the last hide / show / isolate, selection change, model load or removal, selection box or section plane edit",
    group: VIEWER,
  },
  { id: "core:redo", keys: "Ctrl+Y", label: "Redo", group: VIEWER },
  { id: "core:redo-alt", keys: "Ctrl+Shift+Z", label: "Redo (alternative)", group: VIEWER },
  { id: "core:fit-selection", keys: "Shift+F", label: "Center the view on the selection", group: VIEWER, keepDefault: true },
  { id: "core:fit-all", keys: "Shift+A", label: "Zoom to fit the whole model", group: VIEWER, keepDefault: true },
  // The camera from the keyboard. Mouse and touch are mapped in Options > Camera controls.
  { id: "core:view.top", keys: "Alt+1", label: "Camera: top view", group: CAMERA },
  { id: "core:view.bottom", keys: "Alt+2", label: "Camera: bottom view", group: CAMERA },
  { id: "core:view.front", keys: "Alt+3", label: "Camera: front view", group: CAMERA },
  { id: "core:view.back", keys: "Alt+4", label: "Camera: back view", group: CAMERA },
  { id: "core:view.left", keys: "Alt+5", label: "Camera: left view", group: CAMERA },
  { id: "core:view.right", keys: "Alt+6", label: "Camera: right view", group: CAMERA },
  { id: "core:view.isometric", keys: "Alt+7", label: "Camera: isometric view", group: CAMERA },
  { id: "core:reset-camera", keys: "Alt+0", label: "Camera: reset", group: CAMERA },
  { id: "core:orbit-left", keys: "Ctrl+ArrowLeft", label: "Camera: orbit left", group: CAMERA },
  { id: "core:orbit-right", keys: "Ctrl+ArrowRight", label: "Camera: orbit right", group: CAMERA },
  { id: "core:orbit-up", keys: "Ctrl+ArrowUp", label: "Camera: orbit up", group: CAMERA },
  { id: "core:orbit-down", keys: "Ctrl+ArrowDown", label: "Camera: orbit down", group: CAMERA },
  { id: "core:pan-left", keys: "Ctrl+Shift+ArrowLeft", label: "Camera: pan left", group: CAMERA },
  { id: "core:pan-right", keys: "Ctrl+Shift+ArrowRight", label: "Camera: pan right", group: CAMERA },
  { id: "core:pan-up", keys: "Ctrl+Shift+ArrowUp", label: "Camera: pan up", group: CAMERA },
  { id: "core:pan-down", keys: "Ctrl+Shift+ArrowDown", label: "Camera: pan down", group: CAMERA },
  { id: "core:toggle-options", keys: "Shift+Q", label: "Toggle the Options menu", group: VIEWER, keepDefault: true },
  { id: "core:toggle-gallery", keys: "Shift+W", label: "Toggle gallery mode", group: VIEWER },
  { id: "core:toggle-admin", keys: "Shift+P", label: "Toggle the Admin panel (administrators only)", group: VIEWER },
  { id: "core:render-profiling", keys: "Shift+R", label: "Start / end a render-profiling session (administrators only)", group: VIEWER },
  {
    id: "core:tree-focus",
    keys: "Shift+T",
    label: "Open the tree at the selected element and focus it for arrow-key navigation; pressed while the tree is focused, close it",
    group: VIEWER,
  },
  {
    id: "core:copy-names",
    keys: "Shift+C",
    label: "Copy selected names to the clipboard (levels picked in the tree copy as the levels' own names)",
    group: VIEWER,
    keepDefault: true,
  },
  { id: "core:copy-names-ctrl", keys: "Ctrl+C", label: "Same as Shift+C, unless text is highlighted", group: VIEWER },
  { id: "core:select-all", keys: "Ctrl+A", label: "Select every shown element of the loaded models", group: VIEWER },
  { id: "core:open-file", keys: "Ctrl+O", label: "Open a GLB / glTF file from disk in the scene (File → Open…)", group: VIEWER },
  { id: "core:command-palette", keys: "Ctrl+K", label: "Open the command palette: every command, found by name", group: VIEWER },
  {
    id: "core:marking-menu",
    keys: "Shift+E",
    label: "Open the marking menu at the pointer over the 3D view (where the UI has it on; arrows choose, Enter runs, Escape closes)",
    group: VIEWER,
  },
  { id: "core:measure", keys: "Shift+M", label: "Open the Scene panel's Measure tab (distances, angles, paths, coordinates)", group: VIEWER },
  { id: "core:select-parent", keys: "Shift+ArrowUp", label: "Select the parent level of the current selection (tree up)", group: VIEWER },
  { id: "core:select-child", keys: "Shift+ArrowDown", label: "Select the first child level (tree down)", group: VIEWER },
  { id: "core:select-prev-sibling", keys: "Shift+ArrowLeft", label: "Select the previous sibling", group: VIEWER },
  { id: "core:select-next-sibling", keys: "Shift+ArrowRight", label: "Select the next sibling", group: VIEWER },
  // Global / viewer -- local
  {
    id: "core:tree-close",
    keys: "Escape",
    label: "While a tree has focus: close the tree panel (in Sources, first narrows a multi-selection)",
    group: VIEWER,
    local: true,
    reserves: true,
  },
  {
    id: "core:measure-end",
    keys: "Escape",
    label: "While a Measure tool is on: end the measurement being made (a path is kept); pressed again, put the tool down",
    group: VIEWER,
    local: true,
  },
  {
    id: "core:joint-view-exit",
    keys: "Escape",
    label: "While a clash joint is shown with the rest faded or hidden: leave that view (as does a click on empty space)",
    group: VIEWER,
    local: true,
  },
  { id: "core:measure-finish", keys: "Enter", label: "While the Measure tab's Path tool is on: finish the path", group: VIEWER, local: true },
  // Panels -- local (layout/zoneFocus.tsx)
  {
    id: "core:zone-next",
    keys: "F6",
    label: "Move focus to the next area: top bar, panel column, each docked group, each floating panel, the 3D view",
    group: PANELS,
    local: true,
    reserves: true,
    requires: "customisable-panels",
  },
  { id: "core:zone-prev", keys: "Shift+F6", label: "Move focus to the previous area", group: PANELS, local: true, reserves: true, requires: "customisable-panels" },
  {
    id: "core:float-close",
    keys: "Escape",
    label: "While a floating panel has focus (not a text field): close it; focus returns to where it was",
    group: PANELS,
    local: true,
    reserves: true,
    requires: "customisable-panels",
  },
  // Gallery walk -- local (components/viewer/GalleryControls.tsx)
  { id: "core:gallery-prev", keys: "ArrowLeft", label: "Previous item (back onto the previous project's page at its start)", group: GALLERY, local: true },
  { id: "core:gallery-next", keys: "ArrowRight", label: "Next item (onto the next project's page at its end)", group: GALLERY, local: true },
  { id: "core:gallery-prev-project", keys: "PageUp", label: "Previous project (previous file within one project)", group: GALLERY, local: true },
  { id: "core:gallery-next-project", keys: "PageDown", label: "Next project (next file within one project)", group: GALLERY, local: true },
  // Procedural cellbuilder -- Shift+Enter is dispatched, the rest local (components/viewer/cellbuilder/keyboard.ts)
  {
    id: "core:builder-compile",
    keys: "Shift+Enter",
    label: "Preview-compile the current model (when it has uncommitted changes)",
    group: BUILDER,
  },
  { id: "core:builder-translate", keys: "G", label: "Translate gizmo (cell or equipment selected)", group: BUILDER, local: true },
  { id: "core:builder-rotate", keys: "R", label: "Rotate gizmo (equipment only)", group: BUILDER, local: true },
  { id: "core:builder-resize", keys: "S", label: "Resize gizmo (cell, opening or equipment)", group: BUILDER, local: true },
  { id: "core:builder-lock-x", keys: "X", label: "Lock the active gizmo to the X axis (press again to release)", group: BUILDER, local: true },
  { id: "core:builder-lock-y", keys: "Y", label: "Lock the active gizmo to the Y axis (press again to release)", group: BUILDER, local: true },
  { id: "core:builder-lock-z", keys: "Z", label: "Lock the active gizmo to the Z axis (press again to release)", group: BUILDER, local: true },
  { id: "core:builder-hide", keys: "Shift+H", label: "Hide the selected cell(s)", group: BUILDER, local: true },
  { id: "core:builder-unhide", keys: "Shift+U", label: "Unhide all cells", group: BUILDER, local: true },
  { id: "core:builder-floor-up", keys: "PageUp", label: "Move the selected equipment or opening up a floor", group: BUILDER, local: true },
  { id: "core:builder-floor-down", keys: "PageDown", label: "Move the selected equipment or opening down a floor", group: BUILDER, local: true },
  { id: "core:builder-undo", keys: "Ctrl+Z", label: "Undo", group: BUILDER, local: true },
  { id: "core:builder-redo", keys: "Ctrl+Shift+Z", label: "Redo", group: BUILDER, local: true },
  { id: "core:builder-redo-alt", keys: "Ctrl+Y", label: "Redo (alternative)", group: BUILDER, local: true },
  {
    id: "core:builder-back",
    keys: "Escape",
    label: "Step back one layer: context menu → gizmo → add-mode → selection",
    group: BUILDER,
    local: true,
  },
  { id: "core:builder-apply", keys: "Enter", label: "Apply the value typed in the gizmo HUD field", group: BUILDER, local: true },
  // The builder's own tools and walks (keyboard.ts): listed so the dialog is the whole story.
  { id: "core:builder-add-space", keys: "A", label: "Add a space: place it with the pointer, or type X, Y, Z", group: BUILDER, local: true },
  {
    id: "core:builder-loft",
    keys: "L",
    label: "New loft member (on a selected space face: a tube the size of the face, along its normal)",
    group: BUILDER,
    local: true,
  },
  {
    id: "core:builder-insert-equipment",
    keys: "Q",
    label: "Insert equipment from the keyboard: T type, N / P host space, Enter, then type its local X, Y",
    group: BUILDER,
    local: true,
  },
  { id: "core:builder-info", keys: "I", label: "Show the selection's info beside it (dimensions, masses, centre of gravity); press again to hide", group: BUILDER, local: true },
  { id: "core:builder-opening", keys: "O", label: "Opening on the selected space face: type X, Y, W, H and depth", group: BUILDER, local: true },
  {
    id: "core:builder-extrude",
    keys: "E",
    label: "Extrude: a new space from the selected face, or a loft one station further up its spine",
    group: BUILDER,
    local: true,
  },
  { id: "core:builder-loft-resize", keys: "S", label: "On a loft: resize the active station's section (type the value)", group: BUILDER, local: true },
  { id: "core:builder-select-granularity", keys: "Tab", label: "Cycle what a pick selects: space, face, edge", group: BUILDER, local: true },
  { id: "core:builder-next-space", keys: "N", label: "Select the next space (in an equipment insert: the next host)", group: BUILDER, local: true },
  { id: "core:builder-prev-space", keys: "P", label: "Select the previous space (in an equipment insert: the previous host)", group: BUILDER, local: true },
  { id: "core:builder-next-element", keys: "F", label: "Next face or edge of the selection (on a loft: the next station)", group: BUILDER, local: true },
  { id: "core:builder-prev-element", keys: "D", label: "Previous face or edge (on a loft: the previous station)", group: BUILDER, local: true },
  { id: "core:builder-face-up", keys: "ArrowUp", label: "With a space face selected: the neighbouring face above it on screen", group: BUILDER, local: true },
  { id: "core:builder-face-down", keys: "ArrowDown", label: "With a space face selected: the neighbouring face below it on screen", group: BUILDER, local: true },
  { id: "core:builder-face-left", keys: "ArrowLeft", label: "With a space face selected: the neighbouring face to its left on screen", group: BUILDER, local: true },
  { id: "core:builder-face-right", keys: "ArrowRight", label: "With a space face selected: the neighbouring face to its right on screen", group: BUILDER, local: true },
  {
    id: "core:builder-cycle-type",
    keys: "T",
    label: "Cycle the cell type (a loft station: rectangle / circle; an equipment insert: the equipment type)",
    group: BUILDER,
    local: true,
  },
  {
    id: "core:builder-cell-type",
    keys: "1",
    range: digits(1, 9),
    label: "Pick that cell type of the catalog (the first nine)",
    group: BUILDER,
    local: true,
  },
  { id: "core:builder-rep-next", keys: "`", label: "Next representation: topology, simulation, detail", group: BUILDER, local: true },
  { id: "core:builder-rep-prev", keys: "Shift+`", label: "Previous representation", group: BUILDER, local: true },
  { id: "core:builder-rep-topology", keys: "Shift+1", label: "Representation: topology", group: BUILDER, local: true },
  { id: "core:builder-rep-simulation", keys: "Shift+2", label: "Representation: simulation", group: BUILDER, local: true },
  { id: "core:builder-rep-detail", keys: "Shift+3", label: "Representation: detail", group: BUILDER, local: true },
  { id: "core:builder-exclude-face", keys: "Shift+X", label: "On a selected loft face: leave its plate out (press again to put it back)", group: BUILDER, local: true },
  {
    id: "core:builder-delete",
    keys: "Delete",
    label: "Delete the selection (a loft bay: one station off; its last bay: the whole loft)",
    group: BUILDER,
    local: true,
  },
  { id: "core:builder-delete-alt", keys: "Backspace", label: "Delete the selection (as Delete)", group: BUILDER, local: true },
  // Typed values -- local (numericEntry.ts, insertTools.ts): while one is being entered these keys are its own.
  { id: "core:typed-digit", keys: "0", range: digits(0, 9), label: "Type the value (the numpad too)", group: TYPED, local: true },
  { id: "core:typed-decimal", keys: ".", label: "Decimal point (in an extrude also \",\")", group: TYPED, local: true },
  { id: "core:typed-minus", keys: "-", label: "Minus, as the first character", group: TYPED, local: true },
  { id: "core:typed-next-field", keys: ",", label: "Next field: X, Y, Z of a placement; X, Y, W, H, depth of an opening", group: TYPED, local: true },
  { id: "core:typed-backspace", keys: "Backspace", label: "Delete the last character typed", group: TYPED, local: true },
  { id: "core:typed-enter", keys: "Enter", label: "Commit (an opening: the next stage, then commit)", group: TYPED, local: true },
  { id: "core:typed-escape", keys: "Escape", label: "Cancel the entry", group: TYPED, local: true },
];
