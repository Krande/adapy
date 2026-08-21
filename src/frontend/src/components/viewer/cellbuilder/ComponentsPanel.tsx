import React from "react";
import {useCellBuilderStore} from "@/state/cellBuilderStore";
import {btn, btnGray, inputCls} from "./chrome";
import {EmptyState, Ui} from "@/components/ui";

// The procedural model's components: every cell and piece of equipment in it.
//
// This was a collapsed disclosure inside the Builder panel, sharing a narrow right dock
// with grid settings, compile settings and groups — so the list of things the model
// CONTAINS sat among the knobs that control how it is built. Those are different kinds
// of thing: one is the document, the others are settings about the document.
//
// It is a left-dock panel now, which is where a model tree belongs and where the
// Outliner already puts the same idea for loaded geometry. Being the whole panel rather
// than a disclosure also means the list can fill the height instead of capping at 14rem
// and scrolling inside a section that itself scrolls.

export default function ComponentsPanel() {
    const s = useCellBuilderStore();

    if (!s.active) {
        return (
            <EmptyState
                title="No procedural model is open"
                hint={
                    <>
                        Use <Ui>New procedural model…</Ui> in the Build toolbar, or open one from
                        Storage.
                    </>
                }
            />
        );
    }

    return (
        <div className="flex min-h-0 flex-1 flex-col p-2 text-xs">
            <div className="flex min-h-0 flex-1 flex-col gap-1 overflow-y-auto scrollbar">
              {Object.values(s.cells).length === 0 && (
                <p className="italic text-content-muted">
                  No cells yet — use Add cell in the toolbar above to start,
                  or open a template from Storage’s “+” menu.
                </p>
              )}
              {Object.values(s.cells).map((c) => (
                <div
                  key={c.id}
                  className={
                    "flex items-center gap-1 border-b border-edge pb-0.5 cursor-pointer rounded-sm px-0.5 " +
                    (s.selection?.cellId === c.id
                      ? "bg-accent-subtle"
                      : "pointer-fine:hover:bg-surface-2")
                  }
                  onClick={() => s.setSelection({ kind: "cell", cellId: c.id })}
                >
                  <span
                    className="inline-block w-2 h-2 rounded-sm shrink-0"
                    style={{
                      background:
                        c.kind === "cell"
                          ? "#3b82f6"
                          : c.kind === "loft"
                            ? "#14b8a6"
                            : "#f97316",
                    }}
                  />
                  <span
                    className="truncate"
                    title={`${c.origin.map((v) => v.toFixed(2))} / ${c.size.map((v) => v.toFixed(2))}`}
                  >
                    {c.name}
                  </span>
                  {c.kind === "equipment" && (
                    <span className="text-content-muted">
                      {c.equipmentType ?? "generic"}
                    </span>
                  )}
                  {c.kind === "opening" && (
                    <select
                      className="bg-surface-2 text-content text-[11px] rounded-sm px-1"
                      value={c.subtype ?? "door"}
                      onClick={(e) => e.stopPropagation()}
                      onChange={(e) =>
                        s.updateCell(c.id, {
                          subtype: e.target.value as "door" | "window",
                        })
                      }
                      title="door: jambs + lintel + threshold (cut to floor); window: jambs + head + sill (punched at its height)"
                    >
                      <option value="door">door</option>
                      <option value="window">window</option>
                    </select>
                  )}
                  <button
                    className="ml-auto px-1 rounded-sm pointer-fine:hover:bg-surface-3"
                    title="Delete"
                    onClick={(e) => {
                      e.stopPropagation();
                      s.removeCell(c.id);
                    }}
                  >
                    🗑
                  </button>
                </div>
              ))}
            </div>
        </div>
    );
}
