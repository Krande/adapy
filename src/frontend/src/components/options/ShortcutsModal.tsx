import React, { useEffect, useState } from "react";

// Single source of truth for the keyboard-shortcut help map. Grouped by
// the CONTEXT in which the keys are live so the user knows WHERE/WHEN each
// shortcut applies. Adding a shortcut later is a one-line data edit here —
// the modal renders straight from this array.
//
// Inventory mirrors the real handlers:
//   * Global / viewer  → sceneHelpers/setupCameraControlsHandlers.ts
//   * Gallery walk      → viewer/GalleryControls.tsx
//   * Procedural cellbuilder → viewer/CellBuilderController.tsx (capture phase)

interface Shortcut {
  /** One or more key tokens rendered as separate <kbd> chips, e.g.
   *  ["Shift", "H"] → "Shift + H". Alternatives get their own entry. */
  keys: string[];
  description: string;
}

interface ShortcutGroup {
  context: string;
  /** Subtitle telling the user when this group's keys are live. */
  when: string;
  shortcuts: Shortcut[];
}

const SHORTCUT_GROUPS: ShortcutGroup[] = [
  {
    context: "Global / viewer",
    when: "Active whenever the viewer has focus (ignored while typing in a field)",
    shortcuts: [
      { keys: ["Shift", "H"], description: "Hide the current selection" },
      { keys: ["Shift", "U"], description: "Unhide everything" },
      { keys: ["Shift", "F"], description: "Center the view on the selection" },
      { keys: ["Shift", "A"], description: "Zoom to fit the whole model" },
      { keys: ["Shift", "Q"], description: "Toggle the Options menu" },
      { keys: ["Shift", "T"], description: "Toggle the selection tree" },
      {
        keys: ["Shift", "C"],
        description: "Copy selected object names to the clipboard",
      },
      {
        keys: ["Shift", "↑"],
        description: "Select the parent level of the current selection (tree up)",
      },
      { keys: ["Shift", "↓"], description: "Select the first child level (tree down)" },
      { keys: ["Shift", "←"], description: "Select the previous sibling" },
      { keys: ["Shift", "→"], description: "Select the next sibling" },
    ],
  },
  {
    context: "Gallery walk",
    when: "Active while stepping through a files/geometry gallery",
    shortcuts: [
      { keys: ["←"], description: "Previous item" },
      { keys: ["→"], description: "Next item" },
    ],
  },
  {
    context: "Procedural cellbuilder",
    when: "Active when a procedural model's cell builder is open (these keys preempt the global ones)",
    shortcuts: [
      {
        keys: ["G"],
        description: "Translate gizmo (cell or equipment selected)",
      },
      { keys: ["R"], description: "Rotate gizmo (equipment only)" },
      { keys: ["S"], description: "Resize gizmo (cell only)" },
      {
        keys: ["X"],
        description:
          "Lock the active gizmo to the X axis (press again to release)",
      },
      {
        keys: ["Y"],
        description:
          "Lock the active gizmo to the Y axis (press again to release)",
      },
      {
        keys: ["Z"],
        description:
          "Lock the active gizmo to the Z axis (press again to release)",
      },
      { keys: ["Shift", "H"], description: "Hide the selected cell(s)" },
      { keys: ["Shift", "U"], description: "Unhide all cells" },
      {
        keys: ["PageUp"],
        description: "Move the selected equipment or opening up a floor",
      },
      {
        keys: ["PageDown"],
        description: "Move the selected equipment or opening down a floor",
      },
      { keys: ["Ctrl / ⌘", "Z"], description: "Undo" },
      { keys: ["Ctrl / ⌘", "Shift", "Z"], description: "Redo" },
      { keys: ["Ctrl / ⌘", "Y"], description: "Redo (alternative)" },
      {
        keys: ["Esc"],
        description:
          "Step back one layer: context menu → gizmo → add-mode → selection",
      },
      {
        keys: ["Enter"],
        description: "Apply the value typed in the gizmo HUD field",
      },
    ],
  },
];

const Kbd: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <kbd className="inline-block rounded border border-gray-600 bg-gray-800 px-1.5 py-0.5 text-xs font-mono text-gray-100 shadow-sm">
    {children}
  </kbd>
);

const KeyCombo: React.FC<{ keys: string[] }> = ({ keys }) => (
  <span className="inline-flex items-center gap-1 whitespace-nowrap">
    {keys.map((k, i) => (
      <React.Fragment key={i}>
        {i > 0 && <span className="text-gray-500">+</span>}
        <Kbd>{k}</Kbd>
      </React.Fragment>
    ))}
  </span>
);

const ShortcutsModal: React.FC = () => {
  const [open, setOpen] = useState(false);

  // Escape closes the modal (mirrors the other centered dialogs, e.g.
  // WorkerInfoModal). Only wired while open so it doesn't shadow the
  // viewer's own Escape handling.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  return (
    <div>
      <button
        className="bg-blue-700 hover:bg-blue-600 text-white font-semibold py-1 px-2 rounded-sm w-full"
        onClick={() => setOpen(true)}
      >
        Shortcut Keys
      </button>
      {open && (
        <div
          className="fixed inset-0 z-[70] flex items-start sm:items-center justify-center bg-black/70 p-4 overflow-y-auto"
          onClick={() => setOpen(false)}
        >
          <div
            className="bg-gray-900 border border-gray-700 rounded-md shadow-xl flex flex-col w-full max-w-3xl max-h-[85vh] my-auto text-white"
            onClick={(e) => e.stopPropagation()}
            role="dialog"
            aria-modal="true"
            aria-label="Keyboard shortcuts"
          >
            <div className="flex items-center gap-3 border-b border-gray-700 px-4 py-3">
              <h2 className="flex-1 text-base font-bold">Keyboard shortcuts</h2>
              <button
                type="button"
                className="shrink-0 text-gray-300 hover:text-white text-xl leading-none px-2"
                onClick={() => setOpen(false)}
                aria-label="Close"
                title="Close (Esc)"
              >
                ×
              </button>
            </div>

            <div className="flex-1 overflow-y-auto p-4 space-y-6">
              {SHORTCUT_GROUPS.map((group) => (
                <section key={group.context}>
                  <h3 className="text-sm font-semibold text-blue-300">
                    {group.context}
                  </h3>
                  <p className="mb-2 text-xs text-gray-400">{group.when}</p>
                  <ul className="divide-y divide-gray-800 rounded-sm border border-gray-800">
                    {group.shortcuts.map((sc, i) => (
                      <li
                        key={i}
                        className="flex items-center justify-between gap-4 px-3 py-1.5"
                      >
                        <span className="text-sm text-gray-200">
                          {sc.description}
                        </span>
                        <KeyCombo keys={sc.keys} />
                      </li>
                    ))}
                  </ul>
                </section>
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default ShortcutsModal;
