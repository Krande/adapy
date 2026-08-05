import React from "react";

import { useCellBuilderStore } from "@/state/cellBuilderStore";

// Manual per-axis rotation entry for the selected equipment, shown whenever the
// rotate gizmo is active (long-press an equipment → Rotate, or the selection
// panel). The 3D rotation rings give a coarse drag; this panel is the exact
// input — type degrees per axis and hit Rotate. Both write the same absolute
// ROT_X/Y/Z on the cell, so dragging the ring updates the fields live and typing
// a value re-seats the ring. A floating card near the bottom so it clears the
// gizmo and stays reachable on a phone.

const fieldCls =
  "w-16 text-gray-100 bg-gray-700 border border-gray-600 rounded-sm px-1 py-0.5 text-right";
const btn =
  "px-2 py-1 rounded-sm bg-blue-600 text-white disabled:opacity-50 hover:bg-blue-500";
const btnGray = "px-2 py-1 rounded-sm bg-gray-600 text-white hover:bg-gray-500";

const AXES: { key: 0 | 1 | 2; label: string }[] = [
  { key: 0, label: "X" },
  { key: 1, label: "Y" },
  { key: 2, label: "Z" },
];

const CellBuilderRotatePanel: React.FC = () => {
  const gizmoMode = useCellBuilderStore((s) => s.gizmoMode);
  const cell = useCellBuilderStore((s) =>
    s.selection ? s.cells[s.selection.cellId] : null,
  );
  const setCellRotation = useCellBuilderStore((s) => s.setCellRotation);
  const setGizmoMode = useCellBuilderStore((s) => s.setGizmoMode);

  const active = gizmoMode === "rotate" && !!cell && cell.kind === "equipment";
  const cellId = cell?.id ?? null;
  const rot = cell?.rotation ?? [0, 0, 0];
  const r0 = rot[0] ?? 0;
  const r1 = rot[1] ?? 0;
  const r2 = rot[2] ?? 0;

  // Draft strings so a half-typed "-" / "" doesn't fight the store. Re-seeded
  // from the live rotation whenever it changes (e.g. the gizmo ring is dragged)
  // unless a field is mid-edit, so ring + fields stay in lockstep.
  const [draft, setDraft] = React.useState<[string, string, string]>([
    String(r0),
    String(r1),
    String(r2),
  ]);
  const editingRef = React.useRef(false);
  React.useEffect(() => {
    if (editingRef.current) return;
    setDraft([String(r0), String(r1), String(r2)]);
  }, [cellId, r0, r1, r2]);

  if (!active || !cell) return null;

  const apply = () => {
    const parsed = draft.map((d) => {
      const n = Number(d);
      return Number.isFinite(n) ? n : 0;
    }) as [number, number, number];
    setDraft([String(parsed[0]), String(parsed[1]), String(parsed[2])]);
    setCellRotation(cell.id, parsed);
  };

  return (
    <div
      className="pointer-events-auto absolute bottom-24 left-1/2 -translate-x-1/2 z-40 flex flex-col gap-2 rounded-md border border-gray-600 bg-gray-800/95 px-3 py-2 text-sm text-gray-100 shadow-lg"
      onPointerDown={(e) => e.stopPropagation()}
    >
      <div className="flex items-center justify-between gap-4">
        <span className="font-medium">
          Rotate <span className="text-gray-400">{cell.name}</span>
        </span>
        <button
          className={btnGray}
          onClick={() => setGizmoMode("none")}
          title="Close the rotate tool"
        >
          Done
        </button>
      </div>
      <div className="flex items-end gap-2">
        {AXES.map(({ key, label }) => (
          <label key={key} className="flex flex-col items-center gap-0.5">
            <span className="text-xs text-gray-400">{label}°</span>
            <input
              className={fieldCls}
              type="number"
              step={15}
              value={draft[key]}
              onFocus={() => {
                editingRef.current = true;
              }}
              onBlur={() => {
                editingRef.current = false;
              }}
              onChange={(e) => {
                const next: [string, string, string] = [...draft];
                next[key] = e.target.value;
                setDraft(next);
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter") apply();
              }}
            />
          </label>
        ))}
        <button className={btn} onClick={apply} title="Apply the typed angles">
          Rotate
        </button>
      </div>
      <div className="flex items-center gap-2">
        <button
          className={btnGray}
          onClick={() => {
            setDraft(["0", "0", "0"]);
            setCellRotation(cell.id, [0, 0, 0]);
          }}
          title="Reset to axis-aligned"
        >
          Reset
        </button>
        <span className="text-xs text-gray-400">
          drag the rings for a coarse spin, or type exact degrees
        </span>
      </div>
    </div>
  );
};

export default CellBuilderRotatePanel;
