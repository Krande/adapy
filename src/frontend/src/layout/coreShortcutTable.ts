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
}

const VIEWER = "Global / viewer";
const CAMERA = "Camera";
const PANELS = "Panels";
const GALLERY = "Gallery walk";
const BUILDER = "Procedural cellbuilder";

/** The dialog's core groups, in order, with the line saying when their keys are live. */
export const CORE_SHORTCUT_GROUPS: ReadonlyArray<{ group: string; when: string }> = [
  { group: VIEWER, when: "Active whenever the viewer has focus (ignored while typing in a field)" },
  { group: CAMERA, when: "Active whenever the viewer has focus. Mouse and touch are set in Options → Camera controls" },
  { group: PANELS, when: "Active with Options → Theme → Customisable panels on" },
  { group: GALLERY, when: "Active while stepping through a files/geometry gallery" },
  { group: BUILDER, when: "Active when a procedural model's cell builder is open (these keys preempt the global ones)" },
];

export const CORE_SHORTCUTS: readonly CoreShortcutDef[] = [
  // Global / viewer -- dispatched
  { id: "core:hide-selected", keys: "Shift+H", label: "Hide the current selection", group: VIEWER, keepDefault: true },
  { id: "core:hide-others", keys: "Shift+I", label: "Hide everything except the current selection", group: VIEWER, keepDefault: true },
  { id: "core:group-selection", keys: "Shift+G", label: "Save the selection as a named group (shared in the scope)", group: VIEWER },
  { id: "core:show-all", keys: "Shift+U", label: "Unhide everything", group: VIEWER, keepDefault: true },
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
  { id: "core:builder-resize", keys: "S", label: "Resize gizmo (cell only)", group: BUILDER, local: true },
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
];
