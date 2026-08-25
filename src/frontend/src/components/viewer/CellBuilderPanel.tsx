import React from "react";

import { PositionedMenu } from "@/components/common/PositionedMenu";
import DetailingPanel from "@/components/viewer/DetailingPanel";
import {
  useCellBuilderStore,
  type SystemConnection,
} from "@/state/cellBuilderStore";
import { useEquipmentCatalogStore } from "@/state/equipmentCatalogStore";
import { typePickerItems } from "@/utils/cellbuilder/ports";
import { useTypeIconsStore } from "@/state/typeIconsStore";

// The per-scope catalog admin panels are surfaced INLINE inside the Equipment
// and Systems tabs (embedded prop = no floating chrome). Lazily loaded so the
// equipment catalog's WebGL preview only enters the bundle when a catalog tab
// is opened.
const EquipmentAdminPanel = React.lazy(
  () => import("@/components/admin/EquipmentAdminPanel"),
);
const SystemAdminPanel = React.lazy(
  () => import("@/components/admin/SystemAdminPanel"),
);
import { useTreeViewStore } from "@/state/treeViewStore";
import { scopeUrlPart, useScopeStore } from "@/state/scopeStore";
import { followerUrl } from "@/utils/cellbuilder/proceduralChannel";
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

const FACE_LABELS = ["+X", "-X", "+Y", "-Y", "+Z", "-Z"];

// One-line description of what the keyboard tool is doing right now, for the
// Build-tab status row: a live extrude/loft entry (toolHint) wins, else the
// active gizmo, add-mode, or the current selection — so you always know the
// state without guessing.
function describeToolState(
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

// Collapsible viewer for the messages the procedural engine emitted during the
// last compile/preview (logging + stdout, captured worker-side and fetched once
// the job settles — see cellBuilderStore.startCompileJob). Shown for successful
// AND failed compiles so engine errors are inspectable without server access.
const CompileLogSection: React.FC = () => {
  const log = useCellBuilderStore((st) => st.compileLog);
  const runId = useCellBuilderStore((st) => st.compileLogRunId);
  const isCurrentRun = useCellBuilderStore((st) => st.compileLogIsCurrentRun);
  const [copied, setCopied] = React.useState(false);
  const onCopy = () => {
    if (!log) return;
    void navigator.clipboard?.writeText(log).then(
      () => {
        setCopied(true);
        window.setTimeout(() => setCopied(false), 1200);
      },
      () => {},
    );
  };
  return (
    <Section title="Compile log">
      {log ? (
        <>
          <div className="flex items-center gap-2">
            <button
              className={btnGray}
              onClick={onCopy}
              title="Copy the engine log to the clipboard"
            >
              {copied ? "Copied" : "Copy"}
            </button>
            <span
              className="text-gray-500 text-[11px] truncate"
              title={runId ? `compile run ${runId}` : undefined}
            >
              {runId
                ? isCurrentRun
                  ? `engine messages · run ${runId.slice(0, 8)}`
                  : `from an earlier run (${runId.slice(0, 8)}) — this result was cached`
                : "engine messages from the last compile"}
            </span>
          </div>
          <pre className="max-h-56 overflow-auto whitespace-pre-wrap break-words bg-black/40 border border-gray-700 rounded-sm p-1.5 text-[11px] font-mono text-gray-200">
            {log.trim() ? log : "(engine emitted no messages)"}
          </pre>
        </>
      ) : (
        <p className="text-gray-500 text-[12px]">
          No log yet — compile to see engine messages.
        </p>
      )}
    </Section>
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

type PanelTab = "build" | "equipment" | "systems" | "detailing" | "view" | "tools";

const CellBuilderPanel: React.FC = () => {
  const s = useCellBuilderStore();
  const equipBtnRef = React.useRef<HTMLButtonElement>(null);
  const openingBtnRef = React.useRef<HTMLButtonElement>(null);
  const compileCaretRef = React.useRef<HTMLButtonElement>(null);
  const [equipMenuOpen, setEquipMenuOpen] = React.useState(false);
  const [openingMenuOpen, setOpeningMenuOpen] = React.useState(false);
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

  // Load the per-scope catalog for the tab being opened (mirrors what the old
  // "Equipment/System overview" toggle buttons did on open).
  React.useEffect(() => {
    if (tab === "equipment")
      void useEquipmentCatalogStore.getState().refreshEquipment();
    else if (tab === "systems")
      void useEquipmentCatalogStore.getState().refreshSystems();
  }, [tab]);

  // The Detailing tab only exists while a detailing engine is selected — if it is
  // turned back to "none" while that tab is open, fall back to Build so the body
  // isn't left showing nothing.
  const detailingSelected = s.selectedDetailing !== "none";
  React.useEffect(() => {
    if (!detailingSelected)
      setTab((t) => (t === "detailing" ? "build" : t));
  }, [detailingSelected]);

  // Mobile bottom-sheet: the grab handle drags the sheet taller/shorter and a
  // flick down dismisses it. Only the phone layout is a sheet (the handle is
  // sm:hidden), so the drag height is applied only under the sm breakpoint.
  const panelRef = React.useRef<HTMLDivElement>(null);
  // Sheet height is stored in PIXELS (not vh). The drag math works in the
  // visible viewport (window.innerHeight), whereas CSS `vh` on mobile refers to
  // the *large* viewport (browser toolbar retracted) — mixing the two let the
  // sheet grow taller than the visible area and push its grab handle above the
  // top of the screen, out of reach. Pixels keep drag and layout in one space.
  const [sheetPx, setSheetPx] = React.useState<number | null>(null);
  // Lazily seed from matchMedia so the very first render already knows it's
  // mobile — children (e.g. the Cells & equipment section) read this to pick
  // their initial collapsed state, which useState captures once at mount.
  const [isMobile, setIsMobile] = React.useState(
    () =>
      typeof window !== "undefined" &&
      window.matchMedia("(max-width: 639px)").matches,
  );
  // During an active drag we mutate the panel height imperatively (see
  // onGrabMove) instead of via setState — re-rendering this whole panel on every
  // pointermove is what made the drag sluggish on mid-range phones. `livePx`
  // carries the current height across move events so onGrabUp can snap from it.
  const dragRef = React.useRef<{ startY: number; startPx: number; livePx: number } | null>(
    null,
  );
  // Never let the sheet's top rise above this margin from the screen top, so the
  // grab handle (and thus the ability to shrink/dismiss it) is always reachable.
  const TOP_MARGIN = 56;
  const maxSheetPx = () => Math.max(120, (window.innerHeight || 1) - TOP_MARGIN);
  const clampPx = (px: number) => Math.max(80, Math.min(maxSheetPx(), px));
  React.useEffect(() => {
    const mq = window.matchMedia("(max-width: 639px)");
    const on = () => setIsMobile(mq.matches);
    on();
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, []);
  // When the viewport shrinks (mobile toolbar shows, rotation, keyboard) re-clamp
  // so a previously-set height can't leave the handle stranded off-screen.
  React.useEffect(() => {
    const onResize = () =>
      setSheetPx((prev) => (prev == null ? prev : clampPx(prev)));
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);
  const onGrabDown = (e: React.PointerEvent) => {
    const curPx =
      panelRef.current?.getBoundingClientRect().height ??
      (window.innerHeight || 1) * 0.82;
    dragRef.current = { startY: e.clientY, startPx: curPx, livePx: curPx };
    e.currentTarget.setPointerCapture(e.pointerId);
  };
  const onGrabMove = (e: React.PointerEvent) => {
    const d = dragRef.current;
    if (!d) return;
    const px = clampPx(d.startPx + (d.startY - e.clientY)); // drag up ⇒ taller
    d.livePx = px;
    // Imperative height write — no React re-render, so the drag stays smooth
    // even while the panel body is heavy. State is reconciled once on release.
    const el = panelRef.current;
    if (el) {
      el.style.height = `${px}px`;
      el.style.maxHeight = `${px}px`;
    }
  };
  const onGrabUp = (e: React.PointerEvent) => {
    const d = dragRef.current;
    if (!d) return;
    dragRef.current = null;
    e.currentTarget.releasePointerCapture?.(e.pointerId);
    const vh = window.innerHeight || 1;
    if (d.livePx < vh * 0.18) {
      s.setPanelVisible(false); // flicked down small ⇒ dismiss the sheet
      setSheetPx(null);
      return;
    }
    const snaps = [0.32, 0.58, 0.84].map((f) => clampPx(vh * f)); // peek / half / full
    const snapped = snaps.reduce(
      (a, b) => (Math.abs(b - d.livePx) < Math.abs(a - d.livePx) ? b : a),
      snaps[0],
    );
    setSheetPx(snapped);
  };

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
      ref={panelRef}
      style={
        isMobile && sheetPx != null
          ? { height: `${sheetPx}px`, maxHeight: `${sheetPx}px` }
          : undefined
      }
      className={
        CHROME +
        " text-xs pointer-events-auto flex flex-col " +
        // mobile: dock as a bottom sheet; desktop: float in the menu column.
        "fixed inset-x-0 bottom-0 z-30 w-full max-h-[82vh] rounded-t-2xl " +
        "sm:static sm:z-auto sm:w-[340px] sm:max-w-[380px] " +
        "sm:max-h-[calc(100vh-7rem)] sm:rounded-md"
      }
    >
      {/* mobile grab handle — drag to resize the sheet, flick down to dismiss */}
      <div
        className="sm:hidden shrink-0 flex justify-center items-center py-2 cursor-grab active:cursor-grabbing touch-none"
        onPointerDown={onGrabDown}
        onPointerMove={onGrabMove}
        onPointerUp={onGrabUp}
        onPointerCancel={onGrabUp}
        role="separator"
        aria-label="Drag to resize the panel"
      >
        <span
          className="block w-10 h-1.5 rounded-full bg-gray-400/70"
          aria-hidden="true"
        />
      </div>

      {/* ── pinned header ── */}
      <div className="shrink-0 flex items-center gap-2 px-2.5 py-2 border-b border-gray-600/50">
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
      {/* overflow-y-hidden is required: `overflow-x-auto` alone makes the CSS
          overflow-y compute to `auto` too, so at fractional DPI scaling (e.g.
          4K @ 175%) a 1px sub-pixel rounding on the buttons spawns a stray
          vertical scrollbar. shrink-0 keeps the row from being squeezed. */}
      <div
        className="shrink-0 flex gap-1 px-2 pt-1.5 border-b border-gray-600/50 overflow-x-auto overflow-y-hidden"
        role="tablist"
      >
        {tabBtn("build", "Build", cellCount)}
        {tabBtn("equipment", "Equipment")}
        {tabBtn("systems", "Systems", systemCount)}
        {s.selectedDetailing !== "none" && tabBtn("detailing", "Detailing")}
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

          <Section
            title="Cells & equipment"
            count={cellCount}
            // Collapsed by default so the panel opens as compact as possible —
            // expand it when you want to browse/select from the list.
            defaultOpen={false}
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
                <div className="flex flex-col gap-1 pl-2 ml-1 border-l border-gray-600/40">
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
                          <span className="whitespace-nowrap text-gray-300">
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
                          className="flex items-center gap-1 cursor-pointer text-gray-300"
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
                        className="flex items-center gap-1 text-gray-300"
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
                  <div className="text-gray-400 text-xs">
                    {eng?.name ?? s.selectedEngine} compiles a single blueprint;
                    grouping is available with a capability engine.
                  </div>
                ) : (
                  <>
                    {s.groups.length === 0 && (
                      <div className="text-gray-400 text-xs">
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
        </div>

        {/* EQUIPMENT — the per-scope equipment catalog, inline (browse / select
            / manage). Mounted only while active so its WebGL preview and fetch
            spin up on demand. */}
        <div className={tab === "equipment" ? "block" : "hidden"}>
          {tab === "equipment" && (
            <React.Suspense
              fallback={<p className="text-gray-500">Loading catalog…</p>}
            >
              <EquipmentAdminPanel embedded />
            </React.Suspense>
          )}
        </div>

        {/* SYSTEMS — the system-template catalog (inline, on demand) above the
            service-runs inspector. SystemsTab stays mounted (hidden) so its
            auto-highlight effect keeps tracking a freshly-loaded result. */}
        <div className={tab === "systems" ? "block" : "hidden"}>
          {tab === "systems" && (
            <React.Suspense fallback={null}>
              <SystemAdminPanel embedded />
            </React.Suspense>
          )}
          <div className="mt-3 pt-2 border-t border-gray-600/50">
            <div className="font-semibold text-gray-300 mb-1.5">
              Service runs
            </div>
            <SystemsTab />
          </div>
        </div>

        {/* DETAILING — data-driven from the selected engine's joint_types */}
        <div className={tab === "detailing" ? "block" : "hidden"}>
          <DetailingPanel />
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
              ).map(([m, label, title], idx) => (
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
                  title={`${title} — ⇧${idx + 1} jumps here; \` cycles views, ⇧\` reverse`}
                >
                  <span className="opacity-50 mr-0.5">⇧{idx + 1}</span>
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
          <button
            className={btnGray + " self-start"}
            onClick={() => {
              const scope = useScopeStore.getState().current;
              const scopePart = scope ? scopeUrlPart(scope) : "user:me";
              window.open(
                followerUrl(s.active!.modelId, scopePart),
                "_blank",
                "noopener",
              );
            }}
            title="Open a second window that shows this model's compiled result and updates live as you edit here (⇧↵ recompiles a preview). Best across two screens."
          >
            Open result in new window
          </button>

          <button
            className={btnGray + " self-start"}
            onClick={() => s.recenterModel()}
            title="Recompute the model's placement from the current cells so it sits centered in the scene. Use this after deleting a far-off cell/equipment that had skewed the centering."
          >
            Recenter model in scene
          </button>

          <div className="flex items-center gap-3 flex-wrap pt-1 border-t border-gray-600/40">
            <span className="text-gray-400">Compile builds</span>
            <label
              className="inline-flex items-center gap-1 cursor-pointer"
              title="Produce the simulation-level result (plates, beams, systems)"
            >
              <input
                type="checkbox"
                className="accent-blue-600"
                checked={s.buildSim}
                onChange={(e) => s.setBuildSim(e.target.checked)}
              />
              Simulation
            </label>
            <label
              className="inline-flex items-center gap-1 cursor-pointer"
              title="Also produce the high-fidelity detail result (trimmed deck edges, I-girder joints). Switch the representation to Detail to view it."
            >
              <input
                type="checkbox"
                className="accent-blue-600"
                checked={s.buildDetail}
                onChange={(e) => s.setBuildDetail(e.target.checked)}
              />
              Detail
            </label>
          </div>

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

          {/* ── Excel export ── Import lives in the storage panel's "+" menu
              ("Import from Excel…"), since importing creates a NEW procedural
              model rather than editing the one currently open here. */}
          <div className="flex items-center gap-1 flex-wrap">
            <button
              className={btnGray}
              disabled={s.xlsxBusy || !s.active}
              onClick={() => void s.exportToExcel()}
              title="Download the current model as the selected engine's Excel workbook (commits any unsaved edits first). Edit it offline and import it back via the storage panel's + menu."
            >
              {s.xlsxBusy ? "Working…" : "Export to Excel"}
            </button>
            {(s.selectedEngine || "adapy-default") === "adapy-default" && (
              <>
                <button
                  className={btnGray}
                  disabled={s.xlsxBusy || !s.active}
                  onClick={() => void s.exportModel("ifc")}
                  title="Download the DETAIL model as an IFC — beams, plates, joints and equipment, with the clash cuts as IfcRelVoidsElement voids (commits any unsaved edits first)."
                >
                  {s.xlsxBusy ? "Working…" : "Download IFC (detail)"}
                </button>
                <label
                  className="flex items-center gap-1 text-gray-300 text-[11px] cursor-pointer"
                  title="Splice real catalog CAD geometry for equipment in the IFC (off = placeholder boxes). Genie XML always uses the equipment concept type."
                >
                  <input
                    type="checkbox"
                    className="accent-blue-600"
                    checked={s.exportIfcCad}
                    onChange={(e) => s.setExportIfcCad(e.target.checked)}
                  />
                  CAD equip
                </label>
                <button
                  className={btnGray}
                  disabled={s.xlsxBusy || !s.active}
                  onClick={() => void s.exportModel("gxml")}
                  title="Download the SIMULATION model as a Genie concept XML (.gxml) for Sesam GeniE (commits any unsaved edits first)."
                >
                  {s.xlsxBusy ? "Working…" : "Download Genie XML (sim)"}
                </button>
              </>
            )}
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

          <CompileLogSection />
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
            // Always clickable on an active model: an explicit Compile re-consults
            // the server, which is authoritative on cache-vs-rebuild. It rebuilds
            // when anything the last compile depended on has changed — the document
            // itself OR the equipment/system CATALOGS this model draws from (edited
            // in the catalog window, so the doc stays "clean" and needsPreviewCompile
            // can't see it) — and returns the cached result cheaply when nothing has.
            disabled={compileBusy || !s.active}
            onClick={() => void s.compilePreviewSelected()}
            title={
              "Compile a preview of the current model (⇧↵) at the selected level(s) of detail. Rebuilds when the model — or the equipment/system catalog it uses — changed since the last compile; serves the cached result otherwise. Nothing is saved."
            }
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
                onClick: () => void s.compilePreviewSelected(true),
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
