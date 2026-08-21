import {useCellBuilderStore} from "@/state/cellBuilderStore";
import {FACE_LABELS} from "./chrome";

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
