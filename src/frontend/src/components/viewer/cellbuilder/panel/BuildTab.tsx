/**
 * The BUILD tab.
 *
 * Owns: the add-mode buttons and type pickers, the keyboard cheat line and live
 * tool status, grid/snapping, the cell & equipment list, and the compile-settings
 * and groups sections below them.
 */

import React from "react";

import {PositionedMenu} from "@/components/common/PositionedMenu";
import {useCellBuilderStore} from "@/state/cellBuilderStore";
import {typePickerItems} from "@/utils/cellbuilder/ports";
import {Section, btn, describeToolState, inputCls} from "./chrome";
import {CompileSettingsSection} from "./CompileSettingsSection";
import {GroupsSection} from "./GroupsSection";
import {ComponentList} from "./ComponentList";

export const BuildTab: React.FC<{
  equipBtnRef: React.RefObject<HTMLButtonElement | null>;
  openingBtnRef: React.RefObject<HTMLButtonElement | null>;
  equipMenuOpen: boolean;
  setEquipMenuOpen: React.Dispatch<React.SetStateAction<boolean>>;
  openingMenuOpen: boolean;
  setOpeningMenuOpen: React.Dispatch<React.SetStateAction<boolean>>;
  /** The + Cell / Opening / Loft / Equipment row. */
  showAddButtons?: boolean;
  /** The "Cells & equipment" list. */
  showComponentList?: boolean;
}> = ({
  equipBtnRef, openingBtnRef, equipMenuOpen, setEquipMenuOpen, openingMenuOpen, setOpeningMenuOpen,
  showAddButtons = true, showComponentList = true,
}) => {
  const s = useCellBuilderStore();
    const hasCells = Object.values(s.cells).some((c) => c.kind === "cell");
  const cellCount = Object.keys(s.cells).length;
  return (
    <>
      {showAddButtons && (
        <div className="flex items-center gap-1 flex-wrap">
          <button
            className={
              s.mode === "add-cell" ? `${btn} ring-2 ring-blue-300` : btn
            }
            onClick={() =>
              s.setMode(s.mode === "add-cell" ? "idle" : "add-cell")
            }
            title="Click in the scene to place a cell (Esc cancels)"
          >
            + Cell
          </button>
          <button
            ref={openingBtnRef}
            className={
              s.mode === "add-opening" ? `${btn} ring-2 ring-blue-300` : btn
            }
            onClick={() => {
              // Already placing → toggle back to idle; otherwise open the
              // contextual type picker (door/window/… from the engine list).
              if (s.mode === "add-opening") {
                s.setMode("idle");
                return;
              }
              setOpeningMenuOpen((v) => !v);
            }}
            title="Add a door/window opening — pick a type, then click a wall to drop a negative-volume box that cuts the plate it overlaps (Esc cancels)."
          >
            + Opening
          </button>
          {openingMenuOpen && (
            <PositionedMenu
              anchor={{
                kind: "rect",
                getRect: () => openingBtnRef.current?.getBoundingClientRect(),
              }}
              ignoreOutsideRef={openingBtnRef}
              onClose={() => setOpeningMenuOpen(false)}
              header={
                <span className="font-medium text-gray-200">Opening type</span>
              }
              items={
                s.openingTypes.length
                  ? typePickerItems(s.openingTypes).map((it) => ({
                      key: it.key,
                      label: it.label,
                      onClick: () => {
                        s.setSelectedOpeningType(it.slug);
                        s.setMode("add-opening");
                      },
                    }))
                  : [
                      {
                        key: "none",
                        label: "No opening types",
                        disabled: true,
                        onClick: () => {},
                      },
                    ]
              }
            />
          )}
          <button
            className={btn}
            onClick={() => s.addLoftMember()}
            title="Add a loft member (key L) — a 2-station swept surface at the origin. Keys: E extend the stack, F/D cycle stations, S resize section, T rectangle/circle, G move member, Del remove station."
          >
            + Loft
          </button>
          <button
            ref={equipBtnRef}
            className={
              s.mode === "add-equipment" ? `${btn} ring-2 ring-blue-300` : btn
            }
            disabled={
              s.equipmentTypes.length === 0 && s.selectedEquipmentType === null
            }
            onClick={() => {
              // Already placing at cursor → toggle back to idle. Otherwise
              // open the contextual type picker: pick a type to start placing
              // it at the cursor, or seat it onto/into an existing cell.
              if (s.mode === "add-equipment") {
                s.setMode("idle");
                return;
              }
              setEquipMenuOpen((v) => !v);
            }}
            title="Add equipment — pick a type to place at the cursor, or seat it onto/into a cell"
          >
            + Equipment
          </button>
          {equipMenuOpen && (
            <PositionedMenu
              anchor={{
                kind: "rect",
                getRect: () => equipBtnRef.current?.getBoundingClientRect(),
              }}
              ignoreOutsideRef={equipBtnRef}
              onClose={() => setEquipMenuOpen(false)}
              header={
                <span className="font-medium text-gray-200">
                  Equipment type
                </span>
              }
              items={[
                ...(s.equipmentTypes.length
                  ? typePickerItems(s.equipmentTypes).map((it) => ({
                      key: it.key,
                      label: it.label,
                      title: "Place this type at the cursor",
                      onClick: () => {
                        s.setSelectedEquipmentType(it.slug);
                        s.setMode("add-equipment");
                      },
                    }))
                  : [
                      {
                        key: "none",
                        label: "No equipment types",
                        disabled: true,
                        onClick: () => {},
                      },
                    ]),
                {
                  key: "insert",
                  label: "Insert onto/into cell…",
                  separatorBefore: true,
                  disabled: !hasCells || s.equipmentTypes.length === 0,
                  title: hasCells
                    ? "Seat the selected type on a cell's floor or roof, centred on its footprint"
                    : "Add a cell first",
                  onClick: () => {
                    // The insert flow builds the currently-selected type;
                    // default to the first when none is picked yet.
                    if (!s.selectedEquipmentType && s.equipmentTypes[0])
                      s.setSelectedEquipmentType(s.equipmentTypes[0].slug);
                    const r = equipBtnRef.current?.getBoundingClientRect();
                    s.openInsertMenu(
                      r?.left ?? 200,
                      (r?.bottom ?? 200) + 4,
                      null,
                    );
                  },
                },
              ]}
            />
          )}
        </div>
      )}

      {/* Keyboard scheme discoverability — a compact one-liner; the full
          set lives in the + Loft tooltip and the design cheat-sheet. */}
      <div
        className="text-[11px] text-gray-400 leading-snug"
        title="Keyboard-only modelling. Select a face (Tab cycles cell/face/edge); Arrow keys walk to the spatially-adjacent face relative to the camera (F/D cycle as a fallback). E extrudes a new cell from the face — type a depth, Enter commits (chains), Esc cancels. N/P step cells, 1–9 pick cell type, G/R/S move/rotate/resize. I inserts equipment into a cell (T type, N/P cell, Enter, then local X,Y). O adds an opening on the selected face (numeric X,Y,W,H,depth). Lofts: L new, E extend stack, F/D stations, S size, T rectangle/circle."
      >
        Keys: <b>E</b> extrude face · <b>Tab</b> cell/face/edge · <b>↑↓←→</b>{" "}
        walk faces · <b>N/P</b> cells · <b>I</b> equip · <b>O</b> opening ·{" "}
        <b>L</b> loft
      </div>

      {/* Live tool status — which pick mode and what the tool is doing now. */}
      <div className="text-[11px] flex items-center gap-1.5 rounded-sm bg-black/25 border border-gray-700/60 px-2 py-1">
        <span className="text-gray-500">Mode</span>
        <span className="font-semibold text-blue-300 capitalize">
          {s.selectMode}
        </span>
        <span className="text-gray-600">·</span>
        <span className="text-gray-200 truncate" title={describeToolState(s)}>
          {describeToolState(s)}
        </span>
      </div>

      {/* Cell type — the engine-advertised space blueprint + Cell places.
          Shown only when there's a choice; a single type (the built-in room)
          needs no picker, the button just uses it. */}
      {s.cellTypes.length > 1 && (
        <div className="flex items-center gap-1 flex-wrap">
          <span className="text-gray-300">cell</span>
          <select
            className={`${inputCls} flex-1 min-w-0`}
            value={s.selectedCellType ?? ""}
            onChange={(e) => s.setSelectedCellType(e.target.value || null)}
            title="Cell type — the engine-advertised space blueprint the + Cell button places (default size + metadata)"
          >
            {s.cellTypes.map((t) => (
              <option key={t.slug} value={t.slug}>
                {t.name} ({t.origin === "code" ? "code" : "db"})
              </option>
            ))}
          </select>
        </div>
      )}

      {/* Opening & equipment TYPES are chosen contextually now — the
          + Opening / + Equipment buttons open a type-picker popup — so the
          standalone dropdowns are gone. The full catalogs live in the
          Equipment / Systems tabs. */}

      <Section title="Grid & snapping">
        <div className="flex items-center gap-x-2 gap-y-1 flex-wrap">
          <label className="flex items-center gap-1">
            grid
            <input
              type="number"
              step={0.05}
              min={0}
              value={s.gridStep}
              onChange={(e) => s.setGridStep(Number(e.target.value))}
              className={`${inputCls} w-14`}
            />
          </label>
          <label className="flex items-center gap-1">
            snap
            <input
              type="number"
              step={0.05}
              min={0}
              value={s.snapThreshold}
              onChange={(e) => s.setSnapThreshold(Number(e.target.value))}
              className={`${inputCls} w-14`}
            />
          </label>
        </div>
        <span
          className="flex items-center gap-0.5"
          title="What a plain click selects — explicit: the mode decides (cell / face / nearest border edge), no hover auto-pick"
        >
          <span className="text-gray-300 mr-1">select</span>
          {(["none", "cell", "face", "edge"] as const).map((m) => (
            <button
              key={m}
              className={
                "px-1.5 py-0.5 rounded-sm " +
                (s.selectMode === m
                  ? "bg-blue-600 text-white"
                  : "bg-gray-700 text-gray-300 hover:bg-gray-600")
              }
              onClick={() => s.setSelectMode(m)}
              aria-pressed={s.selectMode === m}
            >
              {m}
            </button>
          ))}
        </span>
        <label
          className="flex items-center gap-1"
          title="Vertex snapping: while moving a cell with the translate gizmo, magnetically align its corners onto neighbouring cells' corners (within the snap distance). With an axis lock (X/Y/Z) active, the snap is constrained to that axis only, Blender-style."
        >
          <input
            type="checkbox"
            checked={s.gizmoVertexSnap}
            onChange={(e) => s.setGizmoVertexSnap(e.target.checked)}
          />
          Vertex snap (move)
        </label>
        <label
          className="flex items-center gap-1"
          title="When you translate a space cell, carry the equipment sitting inside it along with the cell (rigid move). Turn off to move a cell without disturbing its equipment."
        >
          <input
            type="checkbox"
            checked={s.moveEquipWithCell}
            onChange={(e) => s.setMoveEquipWithCell(e.target.checked)}
          />
          Move equipment with cell
        </label>
        <label
          className="flex items-center gap-1"
          title="Allow dragging a cell face in the scene to resize it. Off by default — use the Resize gizmo (long-press / right-click a cell, or the selection panel) instead."
        >
          <input
            type="checkbox"
            checked={s.faceDragResize}
            onChange={(e) => s.setFaceDragResize(e.target.checked)}
          />
          Drag faces to resize
        </label>
      </Section>

      {showComponentList && (
        <Section
          title="Cells & equipment"
          count={cellCount}
          // Collapsed by default so the panel opens as compact as possible —
          // expand it when you want to browse/select from the list.
          defaultOpen={false}
        >
          <ComponentList />
        </Section>
      )}


      {/* The equipment & system catalogs live in their own tabs now
        (Equipment / Systems) — no separate overview buttons/panels. */}
      <CompileSettingsSection />
      <GroupsSection />
    </>
  );
};
