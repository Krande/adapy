/**
 * Panel CHROME: shared styling, the collapsible Section, and two small
 * read-only/status helpers.
 *
 * Owns: the CSS token strings every section styles itself with, the ▸ Section
 * used throughout the panel, the one answer to "is this document view-only?"
 * that the panel and the systems tab both read, and the one-line description of
 * what the keyboard tool is doing right now.
 */

import React from "react";

import {capabilities} from "@/services/capabilities";
import {useCellBuilderStore} from "@/state/cellBuilderStore";

// Shared panel chrome uses the same CSS tokens as PANEL_CHROME (themeStore) but
// leaves padding/rounding to the pinned regions below.
export const CHROME =
  "bg-[var(--ada-panel-bg)] border border-[var(--ada-panel-border)] " +
  "text-[var(--ada-panel-text)] shadow-lg";
export const btn =
  "px-2 py-1 rounded-sm bg-blue-600 text-white disabled:opacity-50 hover:bg-blue-500";
export const btnGray =
  "px-2 py-1 rounded-sm bg-gray-600 text-white disabled:opacity-50 hover:bg-gray-500";
export const inputCls =
  "text-gray-100 bg-gray-700 border border-gray-600 rounded-sm px-1 py-0.5";

export const FACE_LABELS = ["+X", "-X", "+Y", "-Y", "+Z", "-Z"];

/** Is the loaded procedural document view-only?
 *
 * True when there is no editable session (`active` is null -- the document came
 * off a GLB via `loadFromDoc` rather than `open`, see those actions), or when
 * the transport serving it cannot commit an edit back
 * (`capabilities.procedural.canEdit`). SAVE_PROCEDURAL_MODEL landed on the
 * websocket transport (ws/REST parity plan, step 3), so `canEdit` there now
 * tracks socket connectivity rather than being permanently false -- a
 * websocket session with a real save path opens with `open()`, not
 * `loadFromDoc()`, precisely so this flips to editable (see
 * `setupModelLoaderAsync`). Not every verb this flag ungates is implemented
 * over every transport, though: a control calling a specific verb should
 * additionally check `capabilities.procedural.supports(verb)` rather than
 * assume `canEdit` alone means that verb works.
 *
 * Shared by the panel and `SystemsTab`, which read the store independently. */
export const isReadOnly = (s: { active: unknown | null }): boolean =>
  !s.active || !capabilities.procedural.canEdit;

// One-line description of what the keyboard tool is doing right now, for the
// Build-tab status row: a live extrude/loft entry (toolHint) wins, else the
// active gizmo, add-mode, or the current selection — so you always know the
// state without guessing.
export function describeToolState(
  s: ReturnType<typeof useCellBuilderStore.getState>,
): string {
  if (s.toolHint) return s.toolHint;
  if (s.gizmoMode !== "none") {
    const g =
      s.gizmoMode === "translate"
        ? "Move"
        : s.gizmoMode === "rotate"
          ? "Rotate"
          : "Resize";
    const lock =
      s.gizmoAxisLock != null ? ` (${["X", "Y", "Z"][s.gizmoAxisLock]})` : "";
    return `${g} gizmo${lock}`;
  }
  if (s.mode === "add-cell") return "Placing cell — click to drop";
  if (s.mode === "add-opening") return "Placing opening — click a wall";
  if (s.mode === "add-equipment") return "Placing equipment — click to drop";
  if (s.selection) {
    const nm = s.cells[s.selection.cellId]?.name ?? "?";
    if (s.selection.kind === "face" && s.selection.faceIndex != null)
      return `${nm} · face ${FACE_LABELS[s.selection.faceIndex] ?? s.selection.faceIndex}`;
    if (s.selection.kind === "edge") return `${nm} · edge`;
    return nm;
  }
  return "Idle";
}

// A collapsible sub-section — the ▸ chevron idiom used throughout the panel.
// Long or occasional groups default closed so the panel stays short.
export const Section: React.FC<{
  title: string;
  count?: number;
  defaultOpen?: boolean;
  children: React.ReactNode;
}> = ({ title, count, defaultOpen = false, children }) => {
  const [open, setOpen] = React.useState(defaultOpen);
  return (
    <div className="border border-gray-600/50 rounded-md bg-black/10 overflow-hidden">
      <button
        className="flex items-center gap-1.5 w-full text-left px-2 py-1.5 hover:bg-gray-700/40"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
      >
        <span
          className={
            "text-gray-400 text-[10px] transition-transform " +
            (open ? "rotate-90" : "")
          }
        >
          ▸
        </span>
        <span className="font-semibold">{title}</span>
        {count != null && (
          <span className="text-gray-400 ml-auto">({count})</span>
        )}
      </button>
      {open && (
        <div className="px-2 pb-2 pt-0.5 flex flex-col gap-2">{children}</div>
      )}
    </div>
  );
};

export type PanelTab = "build" | "equipment" | "systems" | "detailing" | "view" | "tools";
