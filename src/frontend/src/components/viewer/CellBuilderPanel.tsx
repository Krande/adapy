import React from "react";

import { PositionedMenu } from "@/components/common/PositionedMenu";
import {
  useCellBuilderStore,
  type SystemConnection,
} from "@/state/cellBuilderStore";
import { useEquipmentCatalogStore } from "@/state/equipmentCatalogStore";
import { useTypeIconsStore } from "@/state/typeIconsStore";
import { useTreeViewStore } from "@/state/treeViewStore";
import {
  highlightSystems,
  revertSystemHighlight,
  systemColorHex,
} from "@/utils/viewer/systemColors";

// The procedural-modelling context panel: add cells / typed equipment, list the
// boxes, edit systems, commit to postgres (revision-tracked) and compile via
// the worker. The per-selection cell/equipment detail lives in the Selected
// Object Info panel (see CellBuilderSelectionInfo), not here.
//
// Layout: a pinned header (model identity + undo/redo/close), a four-tab body
// (Build · Systems · View · Tools) whose long groups collapse, and a pinned
// footer (Commit + a Compile split-button). ⇧↵ commits and compiles in one
// gesture (see setupCameraControlsHandlers). On desktop the panel floats in the
// menu column; on a phone it docks as a bottom sheet. Toggled from its own
// top-row button in Menu (only rendered while a procedural model is loaded).

// Shared panel chrome uses the same CSS tokens as PANEL_CHROME (themeStore) but
// leaves padding/rounding to the pinned regions below.
const CHROME =
  "bg-[var(--ada-panel-bg)] border border-[var(--ada-panel-border)] " +
  "text-[var(--ada-panel-text)] shadow-lg";
const btn =
  "px-2 py-1 rounded-sm bg-blue-600 text-white disabled:opacity-50 hover:bg-blue-500";
const btnGray =
  "px-2 py-1 rounded-sm bg-gray-600 text-white disabled:opacity-50 hover:bg-gray-500";
const inputCls =
  "text-gray-100 bg-gray-700 border border-gray-600 rounded-sm px-1 py-0.5";

// A collapsible sub-section — the ▸ chevron idiom used throughout the panel.
// Long or occasional groups default closed so the panel stays short.
const Section: React.FC<{
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

// Type-icon overlay toggles: a Factorio-style layer of icons over the model —
// archetype icons on equipment (⚡ electrical, P pump, T tank), fluid/service
// markers along runs (💧 water, black oil drop, ⚡ electrical), and a red "!"
// over equipment with unconnected inputs.
const IconOverlaySection: React.FC = () => {
  const icons = useTypeIconsStore();
  return (
    <div className="flex flex-col gap-1">
      <label className="flex items-center gap-1 font-semibold">
        <input
          type="checkbox"
          checked={icons.enabled}
          onChange={(e) => icons.setEnabled(e.target.checked)}
        />
        Type icons
      </label>
      {icons.enabled && (
        <div className="flex items-center gap-3 flex-wrap pl-5 text-gray-300">
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

// The whole Systems tab body: list the service runs, their type, and which
// equipment ports each connects. Add/remove systems and connections; highlight
// each run in its own colour. Mounted on every tab (hidden when inactive) so the
// auto-highlight effect keeps tinting a freshly-loaded result regardless of the
// visible tab.
const SystemsTab: React.FC = () => {
  const s = useCellBuilderStore();
  const [addSlug, setAddSlug] = React.useState<string | null>(null);
  const [highlighted, setHighlighted] = React.useState(false);
  // A "Procedural model" panel link (clicking a routed run's system) sets
  // focusedSystemName — the parent switches to this tab; scroll+highlight it.
  const focused = s.focusedSystemName;
  const focusedRowRef = React.useRef<HTMLDivElement | null>(null);
  React.useEffect(() => {
    if (focused && focusedRowRef.current)
      focusedRowRef.current.scrollIntoView({ block: "nearest" });
  }, [focused]);
  const equipmentNames = Object.values(s.cells)
    .filter((c) => c.kind === "equipment")
    .map((c) => c.name);
  const systems = Object.values(s.systems);

  // Per-system colour highlighting is ON by default: whenever a compiled result
  // is shown (its draw-ranges resolve once the model tree is built), auto-tint
  // each system with its own colour. Done once per loaded result (tracked by
  // source name) so a manual Revert sticks; reset when the result is unloaded
  // (back to the topology view) so the next result re-highlights.
  const treeData = useTreeViewStore((st) => st.treeData);
  const activeResultSrc = s.detailSourceName ?? s.resultSourceName ?? null;
  const autoHighlightedSrc = React.useRef<string | null>(null);
  React.useEffect(() => {
    if (!activeResultSrc) {
      if (highlighted) {
        revertSystemHighlight();
        setHighlighted(false);
      }
      autoHighlightedSrc.current = null;
      return;
    }
    if (autoHighlightedSrc.current === activeResultSrc || systems.length === 0)
      return;
    const n = highlightSystems(systems.map((sys) => sys.name));
    if (n > 0) {
      autoHighlightedSrc.current = activeResultSrc;
      setHighlighted(true);
    }
  }, [activeResultSrc, treeData, systems.length, highlighted]);
  const effectiveSlug = addSlug ?? s.systemTypes[0]?.slug ?? null;
  const selectedAdd =
    s.systemTypes.find((t) => t.slug === effectiveSlug) ?? null;

  return (
    <div className="flex flex-col gap-1.5">
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
              onChange={(e) => s.updateSystem(sys.id, { name: e.target.value })}
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
              {(["piping", "duct", "cable", "electrical"] as const).map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
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
                    {c.directionVector
                      ? ` ${orientLabel(c.directionVector)}`
                      : ""}
                    )
                  </span>
                </span>
              ) : (
                <span className="truncate">
                  {c.equipment}.<span className="text-gray-300">{c.port}</span>
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
  const [pos, setPos] = React.useState<[string, string, string]>([
    "0",
    "0",
    "0",
  ]);
  const [dir, setDir] = React.useState<"IN" | "OUT">("IN");
  // Orientation: the outward nozzle vector the run leaves the terminal along.
  // A terminal on the x=0 wall should face +X (into the model), etc.
  const [orient, setOrient] = React.useState<keyof typeof ORIENT_VECTORS>("+X");

  const modeBtn = (m: "equip" | "site", label: string) => (
    <button
      key={m}
      className={
        "px-1.5 py-0.5 rounded-sm text-[10px] " +
        (mode === m
          ? "bg-blue-600 text-white"
          : "bg-gray-700 text-gray-300 hover:bg-gray-600")
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
            onChange={(e) =>
              setOrient(e.target.value as keyof typeof ORIENT_VECTORS)
            }
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
                position: [
                  Number(pos[0]) || 0,
                  Number(pos[1]) || 0,
                  Number(pos[2]) || 0,
                ],
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

type PanelTab = "build" | "systems" | "view" | "tools";

const CellBuilderPanel: React.FC = () => {
  const s = useCellBuilderStore();
  const equipBtnRef = React.useRef<HTMLButtonElement>(null);
  const compileCaretRef = React.useRef<HTMLButtonElement>(null);
  const [equipMenuOpen, setEquipMenuOpen] = React.useState(false);
  const [compileMenuOpen, setCompileMenuOpen] = React.useState(false);
  const [tab, setTab] = React.useState<PanelTab>("build");
  const hasCells = Object.values(s.cells).some((c) => c.kind === "cell");
  const cellCount = Object.keys(s.cells).length;
  const systemCount = Object.keys(s.systems).length;

  // Clicking a routed run focuses its system — surface it by switching to the
  // Systems tab (SystemsTab then scrolls it into view).
  const focusedSystem = s.focusedSystemName;
  React.useEffect(() => {
    if (focusedSystem) setTab("systems");
  }, [focusedSystem]);

  if (!s.active || !s.panelVisible) return null;

  const compileState = s.compileJob;
  const compileBusy =
    compileState != null &&
    (compileState.status === "queued" || compileState.status === "running");

  const tabBtn = (id: PanelTab, label: string, badge?: number) => (
    <button
      role="tab"
      aria-selected={tab === id}
      onClick={() => setTab(id)}
      className={
        "px-2.5 py-1.5 rounded-t-md font-semibold flex items-center gap-1 border-b-2 -mb-px whitespace-nowrap " +
        (tab === id
          ? "border-blue-400 text-white"
          : "border-transparent text-gray-400 hover:text-white hover:bg-white/5")
      }
    >
      {label}
      {badge != null && (
        <span className="text-[10px] text-gray-400">{badge}</span>
      )}
    </button>
  );

  return (
    <div
      className={
        CHROME +
        " text-xs pointer-events-auto flex flex-col " +
        // mobile: dock as a bottom sheet; desktop: float in the menu column.
        "fixed inset-x-0 bottom-0 z-30 w-full max-h-[82vh] rounded-t-2xl " +
        "sm:static sm:z-auto sm:w-[340px] sm:max-w-[380px] " +
        "sm:max-h-[calc(100vh-7rem)] sm:rounded-md"
      }
    >
      {/* mobile grab handle */}
      <span
        className="sm:hidden block w-10 h-1 rounded-full bg-gray-500/70 mx-auto mt-2 mb-0.5"
        aria-hidden="true"
      />

      {/* ── pinned header ── */}
      <div className="flex items-center gap-2 px-2.5 py-2 border-b border-gray-600/50">
        <span className="font-semibold truncate" title={s.active.modelId}>
          {s.active.name}
        </span>
        <span className="text-gray-400">r{s.active.revision}</span>
        {s.dirty && (
          <span className="text-amber-400 whitespace-nowrap">● unsaved</span>
        )}
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
          className="px-1 rounded-sm hover:bg-red-500/30"
          title="Close model"
          onClick={s.close}
        >
          ✕
        </button>
      </div>

      {/* ── pinned tab bar ── */}
      <div
        className="flex gap-1 px-2 pt-1.5 border-b border-gray-600/50 overflow-x-auto"
        role="tablist"
      >
        {tabBtn("build", "Build", cellCount)}
        {tabBtn("systems", "Systems", systemCount)}
        {tabBtn("view", "View")}
        {tabBtn("tools", "Tools")}
      </div>

      {/* ── scrollable body ── */}
      <div className="flex-1 overflow-y-auto p-2.5 min-h-0">
        {/* BUILD */}
        <div className={tab === "build" ? "flex flex-col gap-2" : "hidden"}>
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
                // Already placing at cursor → toggle back to idle. Otherwise
                // open the choice menu: place freely at the cursor, or seat it
                // onto/into an existing cell.
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

          <div className="flex items-center gap-1 flex-wrap">
            <select
              className={`${inputCls} flex-1 min-w-0`}
              value={s.selectedEquipmentType ?? ""}
              onChange={(e) =>
                s.setSelectedEquipmentType(e.target.value || null)
              }
              title="Equipment type — built-in archetypes ∪ this scope's DB catalog"
            >
              {s.equipmentTypes.length === 0 && (
                <option value="">no types</option>
              )}
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
            defaultOpen={cellCount > 0 && cellCount <= 12}
          >
            <div className="max-h-56 overflow-y-auto flex flex-col gap-1">
              {Object.values(s.cells).length === 0 && (
                <p className="italic text-gray-400">
                  No cells yet — use + Cell to start, or open a template from the
                  storage “＋ New from template” menu.
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
          </Section>

          <Section title="Catalogs">
            <div className="flex items-center gap-1 flex-wrap">
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
          </Section>
        </div>

        {/* SYSTEMS — kept mounted (hidden) so auto-highlight tracks results */}
        <div className={tab === "systems" ? "block" : "hidden"}>
          <SystemsTab />
        </div>

        {/* VIEW */}
        <div className={tab === "view" ? "flex flex-col gap-2" : "hidden"}>
          <div className="flex items-center gap-1 flex-wrap">
            <span className="text-gray-400 mr-1">Representation</span>
            <span
              className="inline-flex rounded-sm overflow-hidden text-[11px]"
              role="group"
              aria-label="Model representation"
            >
              {(
                [
                  [
                    "topology",
                    "Topology",
                    "The editable cell model (boxes + equipment)",
                  ],
                  [
                    "simulation",
                    "Simulation",
                    "The compiled analysis result (plates, beams, systems)",
                  ],
                  [
                    "detail",
                    "Detail",
                    "The high-fidelity detail model (trimmed deck edges, I-girder joints)",
                  ],
                ] as const
              ).map(([m, label, title]) => (
                <button
                  key={m}
                  className={
                    "px-2 py-0.5 " +
                    (s.repMode === m
                      ? "bg-blue-600 text-white"
                      : "bg-gray-700 text-gray-300 hover:bg-gray-600")
                  }
                  onClick={() => void s.setRepMode(m)}
                  aria-pressed={s.repMode === m}
                  title={title}
                >
                  {label}
                  {s.repMode === m && m !== "topology" && compileBusy
                    ? " …"
                    : ""}
                </button>
              ))}
            </span>
          </div>
          <label
            className="inline-flex items-center gap-1 text-gray-300 cursor-pointer"
            title="Keep the editable topology cells visible underneath the compiled result (result superimposed on topology)"
          >
            <input
              type="checkbox"
              className="accent-blue-600"
              checked={s.superimpose}
              onChange={(e) => void s.setSuperimpose(e.target.checked)}
            />
            Superimpose topology under result
          </label>
          <label
            className="inline-flex items-center gap-1 text-gray-300 cursor-pointer"
            title="Show the compiled result BESIDE the editable topology (offset to the right) instead of on top of it. Edit the topology on the left and watch the result update on the right — ⇧↵ recompiles a preview without committing."
          >
            <input
              type="checkbox"
              className="accent-blue-600"
              checked={s.sideBySide}
              onChange={(e) => s.setSideBySide(e.target.checked)}
            />
            Side-by-side (result beside topology)
          </label>

          <Section title="Overlays">
            <IconOverlaySection />
            <button
              className={
                (s.portsOverlayVisible ? btn : btnGray) + " self-start"
              }
              onClick={() => s.setPortsOverlayVisible(!s.portsOverlayVisible)}
              title="Toggle the port overlay: each equipment's input/output positions and vectors — plus site I/O terminals — drawn as coloured arrows (colours match the equipment catalog)"
              aria-pressed={s.portsOverlayVisible}
            >
              {s.portsOverlayVisible ? "Hide ports" : "Show ports"}
            </button>
          </Section>

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
        </div>

        {/* TOOLS */}
        <div className={tab === "tools" ? "flex flex-col gap-2" : "hidden"}>
          <div className="flex items-center gap-1 flex-wrap">
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
          </div>

          {s.relocations && (
            <div className="border border-amber-500/50 rounded-sm p-1 text-[12px]">
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
            <div className="border border-blue-500/50 rounded-sm p-1 text-[12px]">
              <div className="flex items-center gap-2 mb-1 flex-wrap">
                <span className="font-semibold text-blue-300">
                  Equipment resync
                </span>
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
              {s.resyncSummary.created.length +
                s.resyncSummary.updated.length ===
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
      </div>

      {/* ── error / conflict banners (visible on any tab) ── */}
      {(s.conflict || compileState?.status === "error") && (
        <div className="px-2.5 py-1 border-t border-gray-600/50">
          {s.conflict && <p className="text-red-400">{s.conflict}</p>}
          {compileState?.status === "error" && (
            <p className="text-red-400">
              Compile failed: {compileState.error}
            </p>
          )}
        </div>
      )}

      {/* ── pinned footer ── */}
      <div className="flex items-center gap-2 px-2.5 py-2 border-t border-gray-600/50">
        <span className="inline-flex">
          <button
            className={btn + " rounded-r-none flex items-center gap-1.5"}
            disabled={compileBusy}
            onClick={() => void s.compilePreview()}
            title="Compile a preview of the current, uncommitted model (⇧↵). Nothing is saved — commit only when you're happy with what you see."
          >
            {compileBusy ? `Compiling (${compileState?.status})…` : "Compile"}
            <kbd className="text-[10px] font-semibold bg-white/20 border border-white/25 rounded px-1">
              ⇧↵
            </kbd>
          </button>
          <button
            ref={compileCaretRef}
            className={btn + " rounded-l-none border-l border-white/25 px-1.5"}
            disabled={compileBusy}
            title="More compile options"
            onClick={() => setCompileMenuOpen((v) => !v)}
          >
            ▾
          </button>
        </span>
        {compileMenuOpen && (
          <PositionedMenu
            anchor={{
              kind: "rect",
              getRect: () => compileCaretRef.current?.getBoundingClientRect(),
            }}
            ignoreOutsideRef={compileCaretRef}
            onClose={() => setCompileMenuOpen(false)}
            items={[
              {
                key: "recompile",
                label: "Recompile preview (force)",
                title:
                  "Rebuild the preview even if this doc is cached — use after a compiler/engine change when the document itself hasn't changed",
                onClick: () => void s.compilePreview(true),
              },
              {
                key: "browser",
                label: "Compile in browser (WASM)",
                title:
                  "Compile the current (uncommitted) model in your browser via WebAssembly — no server round-trip. Catalog/CAD equipment falls back to built-in archetypes.",
                onClick: () => void s.compileInBrowser(),
              },
            ]}
          />
        )}
        <button
          className={btnGray}
          disabled={!s.dirty || s.committing}
          onClick={() => void s.commit()}
          title="Commit the current state as a new revision. If you've previewed this exact model, the commit promotes that build — no recompile."
        >
          {s.committing ? "Committing…" : "Commit"}
        </button>
        <span className="ml-auto text-gray-400 whitespace-nowrap">
          r{s.active.revision}
        </span>
      </div>
    </div>
  );
};

export default CellBuilderPanel;
