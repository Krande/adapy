import React from "react";

import { PositionedMenu } from "@/components/common/PositionedMenu";
import {
  useCellBuilderStore,
  type SystemConnection,
} from "@/state/cellBuilderStore";
import { useEquipmentCatalogStore } from "@/state/equipmentCatalogStore";
import { useTypeIconsStore } from "@/state/typeIconsStore";
import {
  highlightSystems,
  revertSystemHighlight,
  systemColorHex,
} from "@/utils/viewer/systemColors";

// The procedural-modelling context panel: add cells / typed equipment, list the
// boxes, edit systems, commit to postgres (revision-tracked) and compile via
// the worker. The per-selection cell/equipment detail lives in the Selected
// Object Info panel (see CellBuilderSelectionInfo), not here — this panel is
// busy enough. Toggled from its own top-row button in Menu (only rendered while
// a procedural model is loaded).

const btn =
  "px-2 py-1 rounded-sm bg-blue-600 text-white disabled:opacity-50 hover:bg-blue-500";
const btnGray =
  "px-2 py-1 rounded-sm bg-gray-600 text-white disabled:opacity-50 hover:bg-gray-500";
const inputCls =
  "text-gray-100 bg-gray-700 border border-gray-600 rounded-sm px-1 py-0.5";


// Systems inspector: list the service runs, their type, and which equipment
// ports each connects. Add/remove systems and connections.
// Type-icon overlay toggles: a Factorio-style layer of icons over the model —
// archetype icons on equipment (⚡ electrical, P pump, T tank), fluid/service
// markers along runs (💧 water, black oil drop, ⚡ electrical), and a red "!"
// over equipment with unconnected inputs.
const IconOverlaySection: React.FC = () => {
  const icons = useTypeIconsStore();
  return (
    <div className="border-t border-gray-600/60 pt-1">
      <div className="flex items-center gap-2 px-1">
        <label className="flex items-center gap-1 font-semibold">
          <input
            type="checkbox"
            checked={icons.enabled}
            onChange={(e) => icons.setEnabled(e.target.checked)}
          />
          Type icons
        </label>
      </div>
      {icons.enabled && (
        <div className="flex items-center gap-3 flex-wrap px-1 pt-1 text-gray-300">
          <label className="flex items-center gap-1">
            <input
              type="checkbox"
              checked={icons.showEquipment}
              onChange={(e) => icons.setShowEquipment(e.target.checked)}
            />
            equipment
          </label>
          <label className="flex items-center gap-1">
            <input
              type="checkbox"
              checked={icons.showMedia}
              onChange={(e) => icons.setShowMedia(e.target.checked)}
            />
            media
          </label>
          <label
            className="flex items-center gap-1"
            title="Red ! over equipment with unconnected inputs"
          >
            <input
              type="checkbox"
              checked={icons.showMissing}
              onChange={(e) => icons.setShowMissing(e.target.checked)}
            />
            missing inputs
          </label>
        </div>
      )}
    </div>
  );
};

const SystemsInspector: React.FC = () => {
  const s = useCellBuilderStore();
  const [open, setOpen] = React.useState(false);
  const [addSlug, setAddSlug] = React.useState<string | null>(null);
  const [highlighted, setHighlighted] = React.useState(false);
  // A "Procedural model" panel link (clicking a routed run's system) sets
  // focusedSystemName — open the inspector and scroll+highlight that system.
  const focused = s.focusedSystemName;
  const focusedRowRef = React.useRef<HTMLDivElement | null>(null);
  React.useEffect(() => {
    if (focused) setOpen(true);
  }, [focused]);
  React.useEffect(() => {
    if (focused && open && focusedRowRef.current)
      focusedRowRef.current.scrollIntoView({ block: "nearest" });
  }, [focused, open]);
  const equipmentNames = Object.values(s.cells)
    .filter((c) => c.kind === "equipment")
    .map((c) => c.name);
  const systems = Object.values(s.systems);
  const effectiveSlug = addSlug ?? s.systemTypes[0]?.slug ?? null;
  const selectedAdd =
    s.systemTypes.find((t) => t.slug === effectiveSlug) ?? null;

  return (
    <div className="border-t border-gray-600/60 pt-1">
      <button
        className="flex items-center gap-1 w-full text-left hover:bg-gray-700/40 rounded-sm px-1"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
      >
        <span className={"transition-transform " + (open ? "rotate-90" : "")}>
          ▸
        </span>
        <span className="font-semibold">Systems</span>
        <span className="text-gray-400">({systems.length})</span>
      </button>
      {open && (
        <div className="flex flex-col gap-1.5 px-1 pt-1">
          {systems.length > 0 && (
            <div className="flex items-center gap-1 flex-wrap">
              <button
                className={
                  "px-1.5 py-0.5 rounded-sm text-white " +
                  (highlighted
                    ? "bg-emerald-600 hover:bg-emerald-500"
                    : "bg-gray-700 hover:bg-gray-600")
                }
                title="Tint each system's routed geometry with its own colour (dims the rest). Needs a compiled model."
                onClick={() => {
                  const n = highlightSystems(systems.map((sys) => sys.name));
                  setHighlighted(n > 0);
                }}
              >
                Highlight systems
              </button>
              <button
                className="px-1.5 py-0.5 rounded-sm bg-gray-700 text-gray-200 hover:bg-gray-600 disabled:opacity-40"
                disabled={!highlighted}
                title="Restore the original geometry colours"
                onClick={() => {
                  revertSystemHighlight();
                  setHighlighted(false);
                }}
              >
                Revert
              </button>
            </div>
          )}
          <div className="flex items-center gap-1 flex-wrap">
            <span className="text-gray-300">add</span>
            <select
              className={inputCls}
              value={effectiveSlug ?? ""}
              onChange={(e) => setAddSlug(e.target.value || null)}
              title="System type — built-in kinds ∪ this scope's DB templates"
            >
              {s.systemTypes.length === 0 && <option value="">no types</option>}
              {s.systemTypes.map((t) => (
                <option key={t.slug} value={t.slug}>
                  {t.name} ({t.origin === "code" ? "code" : "db"})
                </option>
              ))}
            </select>
            <button
              className="px-1.5 py-0.5 rounded-sm bg-gray-700 text-gray-200 hover:bg-gray-600 disabled:opacity-40"
              disabled={!selectedAdd}
              onClick={() =>
                selectedAdd &&
                s.addSystem(
                  selectedAdd.type,
                  selectedAdd.origin === "catalog"
                    ? { name: selectedAdd.name, medium: selectedAdd.medium }
                    : undefined,
                )
              }
            >
              +add
            </button>
            {selectedAdd?.origin === "code" && (
              <button
                className="px-1 rounded-sm text-sky-300 hover:bg-gray-600"
                title="Sync this built-in system kind into the scope's DB catalog"
                onClick={() => void s.syncSystemTypeToDb(selectedAdd.slug)}
              >
                ⤓DB
              </button>
            )}
          </div>
          {systems.length === 0 && (
            <p className="italic text-gray-500">
              No systems. Add one to route a run between equipment ports.
            </p>
          )}
          {systems.map((sys) => (
            <div
              key={sys.id}
              ref={sys.name === focused ? focusedRowRef : undefined}
              className={
                "rounded-sm p-1 flex flex-col gap-1 border " +
                (sys.name === focused
                  ? "border-blue-400 ring-1 ring-blue-400/60"
                  : "border-gray-700/60")
              }
            >
              <div className="flex items-center gap-1">
                <span
                  className="inline-block w-2 h-2 rounded-full shrink-0"
                  title={`${sys.type} · unique system colour`}
                  style={{ background: systemColorHex(sys.name) }}
                />
                <input
                  className={`${inputCls} flex-1 min-w-0`}
                  value={sys.name}
                  onChange={(e) =>
                    s.updateSystem(sys.id, { name: e.target.value })
                  }
                />
                <select
                  className={inputCls}
                  value={sys.type}
                  onChange={(e) =>
                    s.updateSystem(sys.id, {
                      type: e.target.value as typeof sys.type,
                    })
                  }
                >
                  {(["piping", "duct", "cable", "electrical"] as const).map(
                    (t) => (
                      <option key={t} value={t}>
                        {t}
                      </option>
                    ),
                  )}
                </select>
                <button
                  className="px-1 rounded-sm hover:bg-gray-500/40"
                  title="Delete system"
                  onClick={() => s.removeSystem(sys.id)}
                >
                  🗑
                </button>
              </div>
              {sys.connections.map((c, i) => (
                <div key={i} className="flex items-center gap-1 pl-3">
                  <span className="text-gray-400">→</span>
                  {c.site ? (
                    <span
                      className="truncate"
                      title={`Site terminal at ${(c.position ?? [0, 0, 0]).join(", ")}, facing ${(c.directionVector ?? [0, 0, 1]).join(", ")}`}
                    >
                      ⌗ {c.site}{" "}
                      <span className="text-gray-300">
                        (site {c.direction}
                        {c.directionVector ? ` ${orientLabel(c.directionVector)}` : ""})
                      </span>
                    </span>
                  ) : (
                    <span className="truncate">
                      {c.equipment}.
                      <span className="text-gray-300">{c.port}</span>
                    </span>
                  )}
                  <button
                    className="ml-auto px-1 rounded-sm hover:bg-gray-500/40"
                    title="Remove connection"
                    onClick={() => s.removeSystemConnection(sys.id, i)}
                  >
                    ✕
                  </button>
                </div>
              ))}
              <ConnectionAdder
                equipmentNames={equipmentNames}
                onAdd={(conn) => s.addSystemConnection(sys.id, conn)}
              />
            </div>
          ))}
        </div>
      )}
    </div>
  );
};

// Standard archetype ports (kept in sync with ada.topo_model.equipment); a
// free-text fallback covers custom equipment.
const ARCHETYPE_PORTS: Record<string, string[]> = {
  pump: ["suction", "discharge", "power", "signal"],
  tank: ["inlet", "outlet", "signal"],
};

/** Axis labels → outward unit vector for a site terminal's orientation. */
const ORIENT_VECTORS: Record<string, [number, number, number]> = {
  "+X": [1, 0, 0],
  "-X": [-1, 0, 0],
  "+Y": [0, 1, 0],
  "-Y": [0, -1, 0],
  "+Z": [0, 0, 1],
  "-Z": [0, 0, -1],
};

/** Nearest axis label for a direction vector, for compact display (falls back
 * to the raw tuple when it isn't axis-aligned). */
const orientLabel = (v: [number, number, number]): string => {
  for (const [k, av] of Object.entries(ORIENT_VECTORS)) {
    if (av[0] === v[0] && av[1] === v[1] && av[2] === v[2]) return k;
  }
  return v.join(",");
};

const ConnectionAdder: React.FC<{
  equipmentNames: string[];
  onAdd: (conn: SystemConnection) => void;
}> = ({ equipmentNames, onAdd }) => {
  const cells = useCellBuilderStore((st) => st.cells);
  // Endpoint mode: an equipment port, or a site terminal (model-boundary I/O).
  const [mode, setMode] = React.useState<"equip" | "site">("equip");
  const [eq, setEq] = React.useState("");
  const [port, setPort] = React.useState("");
  const eqType = Object.values(cells).find((c) => c.name === eq)?.equipmentType;
  const portOptions = eqType ? (ARCHETYPE_PORTS[eqType] ?? []) : [];
  const [siteName, setSiteName] = React.useState("");
  const [pos, setPos] = React.useState<[string, string, string]>(["0", "0", "0"]);
  const [dir, setDir] = React.useState<"IN" | "OUT">("IN");
  // Orientation: the outward nozzle vector the run leaves the terminal along.
  // A terminal on the x=0 wall should face +X (into the model), etc.
  const [orient, setOrient] = React.useState<keyof typeof ORIENT_VECTORS>("+X");

  const modeBtn = (m: "equip" | "site", label: string) => (
    <button
      key={m}
      className={
        "px-1.5 py-0.5 rounded-sm text-[10px] " +
        (mode === m ? "bg-blue-600 text-white" : "bg-gray-700 text-gray-300 hover:bg-gray-600")
      }
      onClick={() => setMode(m)}
      aria-pressed={mode === m}
    >
      {label}
    </button>
  );

  return (
    <div className="flex flex-col gap-1 pl-3">
      <div className="flex items-center gap-1">
        <span className="text-gray-500 text-[10px]">add</span>
        {modeBtn("equip", "equipment")}
        {modeBtn("site", "site I/O")}
      </div>
      {mode === "equip" ? (
        <div className="flex items-center gap-1">
          <select
            className={`${inputCls} min-w-0 flex-1`}
            value={eq}
            onChange={(e) => {
              setEq(e.target.value);
              setPort("");
            }}
          >
            <option value="">equipment…</option>
            {equipmentNames.map((n) => (
              <option key={n} value={n}>
                {n}
              </option>
            ))}
          </select>
          {portOptions.length > 0 ? (
            <select
              className={inputCls}
              value={port}
              onChange={(e) => setPort(e.target.value)}
            >
              <option value="">port…</option>
              {portOptions.map((p) => (
                <option key={p} value={p}>
                  {p}
                </option>
              ))}
            </select>
          ) : (
            <input
              className={`${inputCls} w-20`}
              placeholder="port"
              value={port}
              onChange={(e) => setPort(e.target.value)}
            />
          )}
          <button
            className="px-1.5 py-0.5 rounded-sm bg-blue-600 text-white disabled:opacity-40"
            disabled={!eq || !port}
            onClick={() => {
              onAdd({ equipment: eq, port });
              setPort("");
            }}
          >
            +
          </button>
        </div>
      ) : (
        <div className="flex items-center gap-1 flex-wrap">
          <input
            className={`${inputCls} w-24`}
            placeholder="site name"
            value={siteName}
            onChange={(e) => setSiteName(e.target.value)}
          />
          {([0, 1, 2] as const).map((i) => (
            <input
              key={i}
              type="number"
              step={0.5}
              className={`${inputCls} w-12`}
              title={["x", "y", "z"][i]}
              value={pos[i]}
              onChange={(e) =>
                setPos((p) => {
                  const next = [...p] as [string, string, string];
                  next[i] = e.target.value;
                  return next;
                })
              }
            />
          ))}
          <select
            className={inputCls}
            value={dir}
            onChange={(e) => setDir(e.target.value as "IN" | "OUT")}
            title="Site input (into the model) or output (off the model)"
          >
            <option value="IN">IN</option>
            <option value="OUT">OUT</option>
          </select>
          <select
            className={inputCls}
            value={orient}
            onChange={(e) => setOrient(e.target.value as keyof typeof ORIENT_VECTORS)}
            title="Orientation — the outward direction the run leaves the terminal along"
          >
            {Object.keys(ORIENT_VECTORS).map((k) => (
              <option key={k} value={k}>
                {k}
              </option>
            ))}
          </select>
          <button
            className="px-1.5 py-0.5 rounded-sm bg-blue-600 text-white disabled:opacity-40"
            disabled={!siteName.trim()}
            onClick={() => {
              onAdd({
                site: siteName.trim(),
                position: [Number(pos[0]) || 0, Number(pos[1]) || 0, Number(pos[2]) || 0],
                direction: dir,
                directionVector: ORIENT_VECTORS[orient],
              });
              setSiteName("");
            }}
          >
            +
          </button>
        </div>
      )}
    </div>
  );
};

const CellBuilderPanel: React.FC = () => {
  const s = useCellBuilderStore();
  const equipBtnRef = React.useRef<HTMLButtonElement>(null);
  const [equipMenuOpen, setEquipMenuOpen] = React.useState(false);
  const [cellsListOpen, setCellsListOpen] = React.useState(true);
  const hasCells = Object.values(s.cells).some((c) => c.kind === "cell");
  const cellCount = Object.keys(s.cells).length;

  if (!s.active || !s.panelVisible) return null;

  const compileState = s.compileJob;
  const compileBusy =
    compileState != null &&
    (compileState.status === "queued" || compileState.status === "running");
  const resultReady =
    compileState != null &&
    (compileState.status === "done" || compileState.status === "cached");

  return (
    <div className="flex flex-col gap-2 text-xs text-white p-2 bg-gray-900/70 rounded-md min-w-[300px] max-w-[380px] pointer-events-auto">
      <div className="flex items-center gap-2">
        <span className="font-semibold truncate" title={s.active.modelId}>
          {s.active.name}
        </span>
        <span className="text-gray-400">r{s.active.revision}</span>
        {s.dirty && <span className="text-amber-400">● unsaved</span>}
        <button
          className="ml-auto px-1 rounded-sm hover:bg-gray-500/40 disabled:opacity-30"
          title="Undo (Ctrl+Z)"
          disabled={s.past.length === 0}
          onClick={s.undo}
        >
          ↶
        </button>
        <button
          className="px-1 rounded-sm hover:bg-gray-500/40 disabled:opacity-30"
          title="Redo (Ctrl+Shift+Z)"
          disabled={s.future.length === 0}
          onClick={s.redo}
        >
          ↷
        </button>
        <button
          className="px-1 rounded-sm hover:bg-gray-500/40"
          title="Close model"
          onClick={s.close}
        >
          ✕
        </button>
      </div>

      <div className="flex items-center gap-1 flex-wrap">
        <button
          className={
            s.mode === "add-cell" ? `${btn} ring-2 ring-blue-300` : btn
          }
          onClick={() => s.setMode(s.mode === "add-cell" ? "idle" : "add-cell")}
          title="Click in the scene to place a cell (Esc cancels)"
        >
          + Cell
        </button>
        <button
          className={
            s.mode === "add-opening" ? `${btn} ring-2 ring-blue-300` : btn
          }
          onClick={() =>
            s.setMode(s.mode === "add-opening" ? "idle" : "add-opening")
          }
          title="Add a door/window opening — click a wall to drop a negative-volume box that cuts the plate it overlaps (Esc cancels). Pick door/window on the placed opening."
        >
          + Opening
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
            // Already placing at cursor → toggle back to idle. Otherwise open
            // the choice menu: place freely at the cursor, or seat it onto/into
            // an existing cell.
            if (s.mode === "add-equipment") {
              s.setMode("idle");
              return;
            }
            setEquipMenuOpen((v) => !v);
          }}
          title="Add equipment — place at the cursor or seat it onto/into a cell"
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
            items={[
              {
                key: "cursor",
                label: "Place at cursor",
                onClick: () => s.setMode("add-equipment"),
              },
              {
                key: "insert",
                label: "Insert onto/into cell…",
                disabled: !hasCells,
                title: hasCells
                  ? "Seat equipment on a cell's floor or roof, centred on its footprint"
                  : "Add a cell first",
                onClick: () => {
                  const r = equipBtnRef.current?.getBoundingClientRect();
                  s.openInsertMenu(r?.left ?? 200, (r?.bottom ?? 200) + 4, null);
                },
              },
            ]}
          />
        )}
        <select
          className={inputCls}
          value={s.selectedEquipmentType ?? ""}
          onChange={(e) => s.setSelectedEquipmentType(e.target.value || null)}
          title="Equipment type — built-in archetypes ∪ this scope's DB catalog"
        >
          {s.equipmentTypes.length === 0 && <option value="">no types</option>}
          {s.equipmentTypes.map((t) => (
            <option key={t.slug} value={t.slug}>
              {t.name} ({t.origin === "code" ? "code" : "db"})
            </option>
          ))}
        </select>
        {(() => {
          const sel = s.equipmentTypes.find(
            (t) => t.slug === s.selectedEquipmentType,
          );
          return sel?.origin === "code" ? (
            <button
              className="px-1 rounded-sm text-sky-300 hover:bg-gray-600"
              title="Sync this built-in archetype into the scope's DB catalog"
              onClick={() => void s.syncEquipmentTypeToDb(sel.slug)}
            >
              ⤓DB
            </button>
          ) : null;
        })()}
      </div>

      <div className="flex items-center gap-1 flex-wrap">
        <span className="text-gray-400 text-[11px]">catalogs</span>
        <button
          className={btnGray}
          onClick={() =>
            useEquipmentCatalogStore.getState().toggleEquipmentPanel()
          }
          title="Open the equipment-type catalog (full admin panel) — the reusable equipment defined for this scope"
        >
          Equipment overview
        </button>
        <button
          className={btnGray}
          onClick={() =>
            useEquipmentCatalogStore.getState().toggleSystemPanel()
          }
          title="Open the system-template catalog (full admin panel) — the reusable system kinds defined for this scope"
        >
          System overview
        </button>
      </div>

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
        <span
          className="flex items-center gap-0.5"
          title="What a plain click selects — explicit: the mode decides (cell / face / nearest border edge), no hover auto-pick"
        >
          <span className="text-gray-300">select</span>
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
          title="Allow dragging a cell face in the scene to resize it. Off by default — use the Resize gizmo (long-press / right-click a cell, or the selection panel) instead."
        >
          <input
            type="checkbox"
            checked={s.faceDragResize}
            onChange={(e) => s.setFaceDragResize(e.target.checked)}
          />
          drag-resize
        </label>
        <label
          className="flex items-center gap-1 ml-auto"
          title="Compile automatically after each commit"
        >
          <input
            type="checkbox"
            checked={s.autoCompile}
            onChange={(e) => s.setAutoCompile(e.target.checked)}
          />
          auto-compile
        </label>
      </div>

      <div className="border-t border-gray-600/60 pt-1">
        <button
          className="flex items-center gap-1 w-full text-left hover:bg-gray-700/40 rounded-sm px-1"
          onClick={() => setCellsListOpen((v) => !v)}
          aria-expanded={cellsListOpen}
        >
          <span
            className={"transition-transform " + (cellsListOpen ? "rotate-90" : "")}
          >
            ▸
          </span>
          <span className="font-semibold">Cells &amp; equipment</span>
          <span className="text-gray-400">({cellCount})</span>
        </button>
      {cellsListOpen && (
      <div className="max-h-48 overflow-y-auto flex flex-col gap-1 pt-1">
        {Object.values(s.cells).length === 0 && (
          <p className="italic text-gray-400">
            No cells yet — use + Cell to start, or{" "}
            <button
              className="underline text-blue-300 hover:text-blue-200"
              onClick={s.loadDemoTemplate}
            >
              add the demo template
            </button>
            .
          </p>
        )}
        {Object.values(s.cells).map((c) => (
          <div
            key={c.id}
            className={
              "flex items-center gap-1 border-b border-gray-600/40 pb-0.5 cursor-pointer rounded-sm px-0.5 " +
              (s.selection?.cellId === c.id
                ? "bg-blue-900/40"
                : "hover:bg-gray-700/40")
            }
            onClick={() => s.setSelection({ kind: "cell", cellId: c.id })}
          >
            <span
              className="inline-block w-2 h-2 rounded-sm"
              style={{ background: c.kind === "cell" ? "#3b82f6" : "#f97316" }}
            />
            <span
              className="truncate"
              title={`${c.origin.map((v) => v.toFixed(2))} / ${c.size.map((v) => v.toFixed(2))}`}
            >
              {c.name}
            </span>
            {c.kind === "equipment" && (
              <span className="text-gray-400">
                {c.equipmentType ?? "generic"}
              </span>
            )}
            {c.kind === "opening" && (
              <select
                className="bg-gray-700 text-gray-100 text-[11px] rounded-sm px-1"
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
              className="ml-auto px-1 rounded-sm hover:bg-gray-500/40"
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
      )}
      </div>

      <SystemsInspector />

      <IconOverlaySection />

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
        title={
          s.designRulesets.find((r) => r.slug === s.designRules)?.description ??
          "Routing/penetration ruleset applied when the model compiles"
        }
      >
        <span className="whitespace-nowrap">Design rules</span>
        <select
          className={inputCls}
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

      {s.conflict && <p className="text-red-400">{s.conflict}</p>}
      {compileState?.status === "error" && (
        <p className="text-red-400">Compile failed: {compileState.error}</p>
      )}

      <div className="flex items-center gap-1 flex-wrap">
        <button
          className={btn}
          disabled={!s.dirty || s.committing}
          onClick={() => void s.commit()}
        >
          {s.committing ? "Committing…" : "Commit"}
        </button>
        <button
          className={btnGray}
          disabled={compileBusy}
          onClick={() => void s.compile()}
        >
          {compileBusy ? `Compiling (${compileState?.status})…` : "Compile"}
        </button>
        <button
          className={btnGray}
          disabled={compileBusy}
          onClick={() => void s.compile(true)}
          title="Rebuild the model even if this revision is already cached — use after a compiler/engine change when the document itself hasn't changed"
        >
          Recompile
        </button>
        <button
          className={btnGray}
          onClick={() => void s.compileInBrowser()}
          title="Compile the current (uncommitted) model in your browser via WebAssembly — no server round-trip. Catalog/CAD equipment falls back to built-in archetypes."
        >
          Compile in browser
        </button>
        {resultReady && compileState && s.resultSourceName === null && (
          <button
            className={btnGray}
            onClick={() => void s.viewResult(compileState.derivedKey)}
            title={compileState.derivedKey}
          >
            View result
          </button>
        )}
        {s.resultSourceName !== null && (
          <button
            className={btnGray}
            onClick={s.hideResult}
            title="Unload the compiled result from the scene"
          >
            Hide result
          </button>
        )}
        <button
          className={btnGray}
          onClick={() => s.setCellsVisible(!s.cellsVisible)}
          title="Toggle the builder cell boxes (hide to focus on the generated structure)"
          aria-pressed={!s.cellsVisible}
        >
          {s.cellsVisible ? "Hide cells" : "Show cells"}
        </button>
        <button
          className={btnGray}
          disabled={s.resyncBusy}
          onClick={() => void s.resyncEquipmentTypes()}
          title="Update this scope's equipment catalog from the built-in code archetypes (new ports, corrected nozzle heights). Recompile afterwards to pick up the changes."
        >
          {s.resyncBusy ? "Resyncing…" : "Resync equipments"}
        </button>
        <button
          className={btnGray}
          disabled={s.relocationBusy}
          onClick={() => void s.proposeRelocations()}
          title="Analyse the model and propose the fewest equipment moves that make its cramped / unroutable runs clean. Nothing moves until you click Apply."
        >
          {s.relocationBusy ? "Analyzing…" : "Propose relocations"}
        </button>
        <button
          className={btnGray}
          onClick={() => s.setPortsOverlayVisible(!s.portsOverlayVisible)}
          title="Toggle the port overlay: each equipment's input/output positions and vectors — plus site I/O terminals — drawn as coloured arrows (colours match the equipment catalog)"
          aria-pressed={s.portsOverlayVisible}
        >
          {s.portsOverlayVisible ? "Hide ports" : "Show ports"}
        </button>
        <button
          className={btnGray}
          onClick={() => {
            if (
              Object.keys(s.cells).length > 0 &&
              !window.confirm(
                "Replace the current cells with the demo template?",
              )
            ) {
              return;
            }
            s.loadDemoTemplate();
          }}
          title="Populate this model with the topo_model demo layout (2 cells, pump/tank pairs, reinforced internal wall)"
        >
          Demo template
        </button>
      </div>

      {s.relocations && (
        <div className="mt-1 border border-amber-500/50 rounded-sm p-1 text-[12px]">
          {s.relocations.proposals.length === 0 ? (
            <p className="text-gray-300">
              {s.relocations.baseline_problems > 0
                ? `No move found; ${s.relocations.unresolved.length} run(s) still unresolvable.`
                : "Routing is clean — no relocations needed."}
            </p>
          ) : (
            <>
              <div className="flex items-center gap-2 mb-1 flex-wrap">
                <span className="font-semibold text-amber-300">
                  {s.relocations.proposals.length} move
                  {s.relocations.proposals.length === 1 ? "" : "s"} proposed
                </span>
                <button
                  className={btn}
                  onClick={() => s.applyRelocations()}
                  title="Move the equipment as proposed (undoable), then recompile to route cleanly"
                >
                  Apply moves
                </button>
                <button
                  className={btnGray}
                  onClick={() =>
                    useCellBuilderStore.setState({ relocations: null })
                  }
                >
                  Dismiss
                </button>
              </div>
              <ul className="flex flex-col gap-0.5">
                {s.relocations.proposals.map((p) => (
                  <li key={p.equipment} className="text-gray-200 break-all">
                    <span className="text-blue-300">{p.equipment}</span>{" "}
                    {p.from.map((v) => v.toFixed(1)).join(",")} →{" "}
                    {p.to.map((v) => v.toFixed(1)).join(",")}
                    <span className="text-gray-500"> — {p.reason}</span>
                  </li>
                ))}
              </ul>
              {s.relocations.unresolved.length > 0 && (
                <p className="text-red-400 mt-0.5">
                  still unresolved: {s.relocations.unresolved.join(", ")}
                </p>
              )}
            </>
          )}
        </div>
      )}

      {s.resyncSummary && (
        <div className="mt-1 border border-blue-500/50 rounded-sm p-1 text-[12px]">
          <div className="flex items-center gap-2 mb-1 flex-wrap">
            <span className="font-semibold text-blue-300">Equipment resync</span>
            <span className="text-gray-400">
              {s.resyncSummary.updated.length} updated,{" "}
              {s.resyncSummary.created.length} added,{" "}
              {s.resyncSummary.unchanged.length} unchanged
              {s.resyncSummary.skipped.length > 0
                ? `, ${s.resyncSummary.skipped.length} skipped`
                : ""}
            </span>
            <button
              className={btnGray}
              onClick={() => s.dismissResyncSummary()}
            >
              Dismiss
            </button>
          </div>
          {s.resyncSummary.created.length + s.resyncSummary.updated.length ===
          0 ? (
            <p className="text-gray-300">
              Catalog already matched the code archetypes — nothing changed.
            </p>
          ) : (
            <ul className="flex flex-col gap-1">
              {[...s.resyncSummary.updated, ...s.resyncSummary.created].map(
                (slug) => (
                  <li key={slug} className="text-gray-200">
                    <span className="text-blue-300">{slug}</span>
                    <span className="text-gray-500">
                      {s.resyncSummary!.created.includes(slug)
                        ? " (new)"
                        : " (updated)"}
                    </span>
                    <ul className="ml-3 list-disc list-inside text-gray-400">
                      {(s.resyncSummary!.changes[slug] ?? []).map((c, i) => (
                        <li key={i} className="break-all">
                          {c}
                        </li>
                      ))}
                    </ul>
                  </li>
                ),
              )}
            </ul>
          )}
        </div>
      )}
    </div>
  );
};

export default CellBuilderPanel;
