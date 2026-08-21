import React from "react";

import { useCellBuilderStore } from "@/state/cellBuilderStore";

// Floating HUD for the active translate / rotate gizmo — the on-screen half of
// the Blender-style keyboard flow (G/R activate, X/Y/Z lock an axis, type a
// value, Enter). It offers:
//   • axis-lock buttons (mirroring the X/Y/Z keys),
//   • a single numeric "delta" field that, once an axis is locked, nudges the
//     selection along/about that axis by the typed amount (Enter applies),
//   • for rotate, the exact per-axis absolute angles too (ROT_X/Y/Z).
// Shown at the bottom so it clears the gizmo and stays thumb-reachable on a
// phone. Rendered only while a translate/rotate gizmo is active.

const fieldCls =
  "w-16 text-content bg-surface-2 border border-edge rounded-sm px-1 py-0.5 text-right disabled:opacity-40";
const btn =
  "px-2 py-1 rounded-sm bg-accent text-white disabled:opacity-50 pointer-fine:hover:bg-accent";
const btnGray = "px-2 py-1 rounded-sm bg-surface-3 text-white pointer-fine:hover:bg-surface-3";
const axisBtn = (on: boolean) =>
  "w-7 py-0.5 rounded-sm " +
  (on
    ? "bg-accent text-white"
    : "bg-surface-2 text-content pointer-fine:hover:bg-surface-3");

const AXES: { key: 0 | 1 | 2; label: string }[] = [
  { key: 0, label: "X" },
  { key: 1, label: "Y" },
  { key: 2, label: "Z" },
];

const CellBuilderGizmoHud: React.FC = () => {
  const gizmoMode = useCellBuilderStore((s) => s.gizmoMode);
  const axisLock = useCellBuilderStore((s) => s.gizmoAxisLock);
  const cell = useCellBuilderStore((s) =>
    s.selection ? s.cells[s.selection.cellId] : null,
  );
  const setGizmoMode = useCellBuilderStore((s) => s.setGizmoMode);
  const setGizmoAxisLock = useCellBuilderStore((s) => s.setGizmoAxisLock);
  const setCellRotation = useCellBuilderStore((s) => s.setCellRotation);
  const translateCellAlongAxis = useCellBuilderStore(
    (s) => s.translateCellAlongAxis,
  );

  const isTranslate = gizmoMode === "translate";
  const isRotate = gizmoMode === "rotate";
  const active =
    !!cell && (isTranslate || (isRotate && cell.kind === "equipment"));

  // Absolute per-axis rotation draft (rotate only), re-seeded from the live
  // rotation unless mid-edit so the rings and the fields stay in lockstep.
  const rot = cell?.rotation ?? [0, 0, 0];
  const r0 = rot[0] ?? 0;
  const r1 = rot[1] ?? 0;
  const r2 = rot[2] ?? 0;
  const cellId = cell?.id ?? null;
  const [absDraft, setAbsDraft] = React.useState<[string, string, string]>([
    String(r0),
    String(r1),
    String(r2),
  ]);
  const editingRef = React.useRef(false);
  React.useEffect(() => {
    if (editingRef.current) return;
    setAbsDraft([String(r0), String(r1), String(r2)]);
  }, [cellId, r0, r1, r2]);

  // The Blender-style locked-axis numeric delta.
  const [delta, setDelta] = React.useState("");
  const deltaRef = React.useRef<HTMLInputElement | null>(null);
  // Focus the delta field as soon as an axis is locked (so "X 2 Enter" works
  // without a click), and clear a stale value when the lock/mode changes.
  React.useEffect(() => {
    setDelta("");
    if (axisLock !== null) deltaRef.current?.focus();
  }, [axisLock, gizmoMode, cellId]);

  if (!active || !cell) return null;

  const unit = isTranslate ? "m" : "°";
  const applyDelta = () => {
    if (axisLock === null) return;
    const v = Number(delta);
    if (!Number.isFinite(v) || v === 0) {
      setDelta("");
      return;
    }
    if (isTranslate) {
      translateCellAlongAxis(cell.id, axisLock, v);
    } else {
      const next: [number, number, number] = [r0, r1, r2];
      next[axisLock] = (rot[axisLock] ?? 0) + v;
      setCellRotation(cell.id, next);
    }
    // Keep focus for chained entries ("X 2 Enter, 2 Enter, …").
    setDelta("");
    deltaRef.current?.focus();
  };

  const applyAbs = () => {
    const parsed = absDraft.map((d) => {
      const n = Number(d);
      return Number.isFinite(n) ? n : 0;
    }) as [number, number, number];
    setAbsDraft([String(parsed[0]), String(parsed[1]), String(parsed[2])]);
    setCellRotation(cell.id, parsed);
  };

  const axisLabel = axisLock === null ? "" : AXES[axisLock].label;

  return (
    <div
      className="pointer-events-auto absolute bottom-24 left-1/2 -translate-x-1/2 z-40 flex flex-col gap-2 rounded-md border border-edge bg-surface-0 px-3 py-2 text-sm text-content shadow-lg"
      onPointerDown={(e) => e.stopPropagation()}
    >
      <div className="flex items-center justify-between gap-4">
        <span className="font-medium">
          {isTranslate ? "Move" : "Rotate"}{" "}
          <span className="text-content-muted">{cell.name}</span>
        </span>
        <button
          className={btnGray}
          onClick={() => setGizmoMode("none")}
          title="Close the gizmo (Esc)"
        >
          Done
        </button>
      </div>

      {/* Axis lock + locked-axis numeric delta (the G/R · X/Y/Z · value flow). */}
      <div className="flex items-center gap-2">
        <span className="text-xs text-content-muted">axis</span>
        {AXES.map(({ key, label }) => (
          <button
            key={key}
            className={axisBtn(axisLock === key)}
            onClick={() => setGizmoAxisLock(axisLock === key ? null : key)}
            title={`Lock to ${label} (key ${label})`}
          >
            {label}
          </button>
        ))}
        <input
          ref={deltaRef}
          className={fieldCls}
          type="number"
          step={isTranslate ? 0.1 : 15}
          placeholder="Δ"
          disabled={axisLock === null}
          value={delta}
          onChange={(e) => setDelta(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") applyDelta();
            else if (e.key === "Escape") deltaRef.current?.blur();
          }}
          title={
            axisLock === null
              ? "Lock an axis (X/Y/Z) to enter a value"
              : `${isTranslate ? "Move along" : "Rotate about"} ${axisLabel} by this much, then Enter`
          }
        />
        <button
          className={btn}
          onClick={applyDelta}
          disabled={axisLock === null}
          title="Apply the delta along the locked axis"
        >
          {isTranslate ? "Move" : "Rotate"}
        </button>
        <span className="text-xs text-content-muted">
          {axisLock === null ? "lock an axis" : `Δ ${axisLabel} [${unit}]`}
        </span>
      </div>

      {/* Rotate also exposes the exact absolute per-axis angles. */}
      {isRotate && (
        <div className="flex items-end gap-2 border-t border-edge pt-2">
          <span className="text-xs text-content-muted">set°</span>
          {AXES.map(({ key, label }) => (
            <label key={key} className="flex flex-col items-center gap-0.5">
              <span className="text-xs text-content-muted">{label}</span>
              <input
                className={fieldCls}
                type="number"
                step={15}
                value={absDraft[key]}
                onFocus={() => {
                  editingRef.current = true;
                }}
                onBlur={() => {
                  editingRef.current = false;
                }}
                onChange={(e) => {
                  const next: [string, string, string] = [...absDraft];
                  next[key] = e.target.value;
                  setAbsDraft(next);
                }}
                onKeyDown={(e) => {
                  if (e.key === "Enter") applyAbs();
                }}
              />
            </label>
          ))}
          <button className={btn} onClick={applyAbs} title="Set exact angles">
            Rotate
          </button>
          <button
            className={btnGray}
            onClick={() => {
              setAbsDraft(["0", "0", "0"]);
              setCellRotation(cell.id, [0, 0, 0]);
            }}
            title="Reset to axis-aligned"
          >
            Reset
          </button>
        </div>
      )}
    </div>
  );
};

export default CellBuilderGizmoHud;
