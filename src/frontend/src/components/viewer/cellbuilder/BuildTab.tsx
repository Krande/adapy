import React from "react";
import { PositionedMenu } from "@/components/common/PositionedMenu";
import { useCellBuilderStore } from "@/state/cellBuilderStore";
import { typePickerItems } from "@/utils/cellbuilder/ports";
import { scopeUrlPart, useScopeStore } from "@/state/scopeStore";
import { followerUrl } from "@/utils/cellbuilder/proceduralChannel";
import {
  CompileLogSection,
  IconOverlaySection,
  Section,
  describeToolState,
  btn,
  btnGray,
  inputCls,
} from ".";

// The Build tab body — add cells / openings / equipment, the live tool-state row, and
// the cell + equipment lists. Moved verbatim out of CellBuilderPanel; the shell still
// owns the `tab === "build"` wrapper so this stays mounted when another tab is shown.
//
// The two dropdown-menu states moved in with it: nothing outside this tab ever read
// them.
export const BuildTab: React.FC = () => {
  const s = useCellBuilderStore();
  const equipBtnRef = React.useRef<HTMLButtonElement>(null);
  const openingBtnRef = React.useRef<HTMLButtonElement>(null);
  const [equipMenuOpen, setEquipMenuOpen] = React.useState(false);
  const [openingMenuOpen, setOpeningMenuOpen] = React.useState(false);
  const hasCells = Object.values(s.cells).some((c) => c.kind === "cell");
  const cellCount = Object.keys(s.cells).length;
  return (
    <>
          <div className="flex items-center gap-1 flex-wrap">
            <button
              className={
                s.mode === "add-cell" ? `${btn} ring-2 ring-accent` : btn
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
                s.mode === "add-opening" ? `${btn} ring-2 ring-accent` : btn
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
                  <span className="font-medium text-content">Opening type</span>
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
                s.mode === "add-equipment" ? `${btn} ring-2 ring-accent` : btn
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
                  <span className="font-medium text-content">
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

          {/* Keyboard scheme discoverability — a compact one-liner; the full
              set lives in the + Loft tooltip and the design cheat-sheet. */}
          <div
            className="text-[11px] text-content-muted leading-snug"
            title="Keyboard-only modelling. Select a face (Tab cycles cell/face/edge); Arrow keys walk to the spatially-adjacent face relative to the camera (F/D cycle as a fallback). E extrudes a new cell from the face — type a depth, Enter commits (chains), Esc cancels. N/P step cells, 1–9 pick cell type, G/R/S move/rotate/resize. I inserts equipment into a cell (T type, N/P cell, Enter, then local X,Y). O adds an opening on the selected face (numeric X,Y,W,H,depth). Lofts: L new, E extend stack, F/D stations, S size, T rectangle/circle."
          >
            Keys: <b>E</b> extrude face · <b>Tab</b> cell/face/edge · <b>↑↓←→</b>{" "}
            walk faces · <b>N/P</b> cells · <b>I</b> equip · <b>O</b> opening ·{" "}
            <b>L</b> loft
          </div>

          {/* Live tool status — which pick mode and what the tool is doing now. */}
          <div className="text-[11px] flex items-center gap-1.5 rounded-sm bg-black/25 border border-edge px-2 py-1">
            <span className="text-content-subtle">Mode</span>
            <span className="font-semibold text-accent capitalize">
              {s.selectMode}
            </span>
            <span className="text-content-subtle">·</span>
            <span className="text-content truncate" title={describeToolState(s)}>
              {describeToolState(s)}
            </span>
          </div>

          {/* Cell type — the engine-advertised space blueprint + Cell places.
              Shown only when there's a choice; a single type (the built-in room)
              needs no picker, the button just uses it. */}
          {s.cellTypes.length > 1 && (
            <div className="flex items-center gap-1 flex-wrap">
              <span className="text-content">cell</span>
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
              <span className="text-content mr-1">select</span>
              {(["none", "cell", "face", "edge"] as const).map((m) => (
                <button
                  key={m}
                  className={
                    "px-1.5 py-0.5 rounded-sm " +
                    (s.selectMode === m
                      ? "bg-accent text-white"
                      : "bg-surface-2 text-content hover:bg-surface-3")
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

          <Section
            title="Cells & equipment"
            count={cellCount}
            // Collapsed by default so the panel opens as compact as possible —
            // expand it when you want to browse/select from the list.
            defaultOpen={false}
          >
            <div className="max-h-56 overflow-y-auto flex flex-col gap-1">
              {Object.values(s.cells).length === 0 && (
                <p className="italic text-content-muted">
                  No cells yet — use + Cell to start, or open a template from the
                  storage “＋ New from template” menu.
                </p>
              )}
              {Object.values(s.cells).map((c) => (
                <div
                  key={c.id}
                  className={
                    "flex items-center gap-1 border-b border-edge pb-0.5 cursor-pointer rounded-sm px-0.5 " +
                    (s.selection?.cellId === c.id
                      ? "bg-accent-subtle"
                      : "hover:bg-surface-2")
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
                    className="ml-auto px-1 rounded-sm hover:bg-surface-3"
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
          </Section>

          {/* The equipment & system catalogs live in their own tabs now
              (Equipment / Systems) — no separate overview buttons/panels. */}

          <Section title="Compile settings">
            <label
              className="flex items-center gap-1"
              title={
                s.engines.find((e) => e.slug === s.selectedEngine)
                  ?.description ??
                "Procedural engine that compiles the model (built-in, or a registered external engine)"
              }
            >
              <span className="whitespace-nowrap">Engine</span>
              <select
                className={`${inputCls} flex-1 min-w-0`}
                value={s.selectedEngine}
                onChange={(e) => s.setSelectedEngine(e.target.value)}
              >
                {s.engines.length === 0 && (
                  <option value={s.selectedEngine}>{s.selectedEngine}</option>
                )}
                {s.engines.map((e) => (
                  <option
                    key={e.slug}
                    value={e.slug}
                    title={e.description ?? undefined}
                  >
                    {e.name} ({e.origin})
                  </option>
                ))}
              </select>
            </label>
            <label
              className="flex items-center gap-1"
              title={
                s.blueprints.find((b) => b.slug === s.selectedBlueprint)
                  ?.description ??
                "Structural blueprint the selected engine compiles the cells with (sets doc.blueprint_name)"
              }
            >
              <span className="whitespace-nowrap">Blueprint</span>
              <select
                className={`${inputCls} flex-1 min-w-0`}
                value={s.selectedBlueprint ?? ""}
                onChange={(e) => s.setSelectedBlueprint(e.target.value)}
              >
                {s.blueprints.length === 0 && (
                  <option value={s.selectedBlueprint ?? ""}>
                    {s.selectedBlueprint ?? "steel_stru"}
                  </option>
                )}
                {s.blueprints.map((b) => (
                  <option key={b.slug} value={b.slug} title={b.description}>
                    {b.name}
                  </option>
                ))}
              </select>
            </label>

            {/* Advertised blueprint parameters (doc.blueprint) — generated from
                the selected blueprint's `fields`. For steel_stru these are the
                girder/column/stringer section enums: pick a `BG…` box or `TUB…`
                tube to build the frame in box beams instead of I-beams. */}
            {(() => {
              const bp = s.blueprints.find(
                (b) => b.slug === s.selectedBlueprint,
              );
              const fields = bp?.fields ?? [];
              if (fields.length === 0) return null;
              return (
                <div className="flex flex-col gap-1 pl-2 ml-1 border-l border-edge">
                  {fields.map((f) => {
                    const cur = s.blueprintOptions[f.name] ?? f.default;
                    const label =
                      (f.label ?? f.name) + (f.unit ? ` (${f.unit})` : "");
                    const title = `Sets doc.blueprint.${f.name}`;
                    if (f.type === "enum") {
                      return (
                        <label
                          key={f.name}
                          className="flex items-center gap-1"
                          title={title}
                        >
                          <span className="whitespace-nowrap text-content">
                            {label}
                          </span>
                          <select
                            className={`${inputCls} flex-1 min-w-0`}
                            value={String(cur)}
                            onChange={(e) =>
                              s.setBlueprintOption(f.name, e.target.value)
                            }
                          >
                            {(f.options ?? []).map((o) => (
                              <option key={o} value={o}>
                                {o}
                              </option>
                            ))}
                          </select>
                        </label>
                      );
                    }
                    if (f.type === "bool") {
                      return (
                        <label
                          key={f.name}
                          className="flex items-center gap-1 cursor-pointer text-content"
                          title={title}
                        >
                          <input
                            type="checkbox"
                            className="accent-blue-600"
                            checked={Boolean(cur)}
                            onChange={(e) =>
                              s.setBlueprintOption(f.name, e.target.checked)
                            }
                          />
                          <span>{label}</span>
                        </label>
                      );
                    }
                    return (
                      <label
                        key={f.name}
                        className="flex items-center gap-1 text-content"
                        title={title}
                      >
                        <span className="whitespace-nowrap">{label}</span>
                        <input
                          type="number"
                          className={inputCls}
                          value={
                            Number.isFinite(Number(cur)) ? Number(cur) : ""
                          }
                          min={f.min}
                          max={f.max}
                          step="any"
                          onChange={(e) => {
                            const n = Number(e.target.value);
                            if (Number.isFinite(n))
                              s.setBlueprintOption(f.name, n);
                          }}
                        />
                      </label>
                    );
                  })}
                </div>
              );
            })()}
            <label
              className="flex items-center gap-1"
              title={
                s.designRulesets.find((r) => r.slug === s.designRules)
                  ?.description ??
                "Routing/penetration ruleset applied when the model compiles"
              }
            >
              <span className="whitespace-nowrap">Design rules</span>
              <select
                className={`${inputCls} flex-1 min-w-0`}
                value={s.designRules}
                onChange={(e) => s.setDesignRules(e.target.value)}
              >
                {s.designRulesets.length === 0 && (
                  <option value={s.designRules}>{s.designRules}</option>
                )}
                {s.designRulesets.map((r) => (
                  <option key={r.slug} value={r.slug} title={r.description}>
                    {r.name} ({r.origin})
                  </option>
                ))}
              </select>
            </label>
            <label
              className="flex items-center gap-1"
              title={
                s.detailingEngines.find((d) => d.slug === s.selectedDetailing)
                  ?.description ??
                "Detailing engine — the fabrication-detail stage that adds connection joints after the structural compile (none = structural-only)"
              }
            >
              <span className="whitespace-nowrap">Detailing</span>
              <select
                className={`${inputCls} flex-1 min-w-0`}
                value={s.selectedDetailing}
                onChange={(e) => s.setSelectedDetailing(e.target.value)}
              >
                {s.detailingEngines.length === 0 && (
                  <option value={s.selectedDetailing}>
                    {s.selectedDetailing}
                  </option>
                )}
                {s.detailingEngines.map((d) => (
                  <option key={d.slug} value={d.slug} title={d.description}>
                    {d.name} ({d.origin})
                  </option>
                ))}
              </select>
            </label>
            <label
              className="flex items-center gap-1"
              title="Replace equipment boxes in the compiled model with the actual CAD geometry (for catalog equipment that have a linked CAD asset)"
            >
              <input
                type="checkbox"
                checked={s.equipmentCad}
                onChange={(e) => s.setEquipmentCad(e.target.checked)}
              />
              Use CAD models for equipment
            </label>
            <label
              className="flex items-center gap-1"
              title="Compile automatically after each commit"
            >
              <input
                type="checkbox"
                checked={s.autoCompile}
                onChange={(e) => s.setAutoCompile(e.target.checked)}
              />
              Auto-compile after commit
            </label>
          </Section>

          {/* GROUPS — per-group blueprints. A group is one structure compiled
              with its own blueprint; only meaningful for engines that advertise
              `supports_grouping` (driven off the flag, never a hardcoded slug). */}
          {(() => {
            const eng = s.engines.find((e) => e.slug === s.selectedEngine);
            const supportsGrouping = Boolean(eng?.supports_grouping);
            return (
              <Section
                title="Groups"
                count={supportsGrouping ? s.groups.length : undefined}
              >
                {!supportsGrouping ? (
                  <div className="text-content-muted text-xs">
                    {eng?.name ?? s.selectedEngine} compiles a single blueprint;
                    grouping is available with a capability engine.
                  </div>
                ) : (
                  <>
                    {s.groups.length === 0 && (
                      <div className="text-content-muted text-xs">
                        No groups — every cell uses the model-level blueprint. Add
                        a group to give a set of cells its own blueprint.
                      </div>
                    )}
                    {s.groups.map((g) => (
                      <div key={g.name} className="flex items-center gap-1">
                        <input
                          type="text"
                          className={`${inputCls} flex-1 min-w-0`}
                          defaultValue={g.name}
                          key={g.name}
                          onBlur={(e) => s.renameGroup(g.name, e.target.value)}
                          onKeyDown={(e) => {
                            if (e.key === "Enter") e.currentTarget.blur();
                          }}
                          title="Group name (also the compiled structure name)"
                        />
                        <select
                          className={`${inputCls} min-w-0`}
                          value={g.blueprint}
                          onChange={(e) =>
                            s.setGroupBlueprint(g.name, e.target.value)
                          }
                          title="Structural blueprint this group compiles with"
                        >
                          {!s.blueprints.some((b) => b.slug === g.blueprint) && (
                            <option value={g.blueprint}>
                              {g.blueprint || "—"}
                            </option>
                          )}
                          {s.blueprints.map((b) => (
                            <option
                              key={b.slug}
                              value={b.slug}
                              title={b.description}
                            >
                              {b.name}
                            </option>
                          ))}
                        </select>
                        <button
                          className={btnGray}
                          onClick={() => s.removeGroup(g.name)}
                          title="Delete group (unassigns its cells)"
                        >
                          ✕
                        </button>
                      </div>
                    ))}
                    <button className={btnGray} onClick={() => s.addGroup()}>
                      + Add group
                    </button>
                  </>
                )}
              </Section>
            );
          })()}
    </>
  );
};
