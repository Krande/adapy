import React from "react";

import { PositionedMenu } from "@/components/common/PositionedMenu";
import DetailingPanel from "@/components/viewer/DetailingPanel";
import { useCellBuilderStore } from "@/state/cellBuilderStore";
import { useEquipmentCatalogStore } from "@/state/equipmentCatalogStore";
import { typePickerItems } from "@/utils/cellbuilder/ports";

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
import { scopeUrlPart, useScopeStore } from "@/state/scopeStore";
import { followerUrl } from "@/utils/cellbuilder/proceduralChannel";
import {EmptyState, Ui} from "@/components/ui";

// Everything below the header used to live in this file — 1963 lines of panel shell,
// six tab bodies and five helper components in one scope. Split out verbatim (no
// behaviour change) so the parts are separately readable and separately re-chromable.
import {
  BuildTab,
  SystemsTab,
  ToolsTab,
  btn,
  btnGray,
  CHROME,
} from "./cellbuilder";

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


// No "view" tab. Its contents went three ways: the view state (representation,
// superimpose, side-by-side, port overlay, recentre) to View ▸ Builder in the menu bar,
// where a View menu is what people look in; the two compile-output toggles to Compile
// settings, which is what they actually control; and nothing was left over.
type PanelTab = "build" | "equipment" | "systems" | "detailing" | "tools";

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

  // No panelVisible check. The dock decides whether a panel is on screen, as it does for
  // every other panel; gating here meant a docked Builder could render an empty box with
  // the reason invisible.
  //
  // `active` is different — that is "no model open", which is content — so it says so
  // rather than returning null. A docked panel that draws nothing is indistinguishable
  // from one that crashed, which is the same complaint that removed the flag above.
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
          ? "border-accent text-white"
          : "border-transparent text-content-muted hover:text-white hover:bg-white/5")
      }
    >
      {label}
      {badge != null && (
        <span className="text-[10px] text-content-muted">{badge}</span>
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
          className="block w-10 h-1.5 rounded-full bg-surface-3"
          aria-hidden="true"
        />
      </div>

      {/* ── pinned header ── */}
      <div className="shrink-0 flex items-center gap-2 px-2.5 py-2 border-b border-edge">
        <span className="font-semibold truncate" title={s.active.modelId}>
          {s.active.name}
        </span>
        <span className="text-content-muted">r{s.active.revision}</span>
        {s.dirty && (
          <span className="text-warn whitespace-nowrap">● unsaved</span>
        )}
        {/* No undo/redo here. They are in the left rail, which is where undo lives in
            every application anyone has used, and having them in both places meant two
            controls for one stack — differently drawn, differently placed, and one of
            them only reachable while this panel happened to be open. */}
        <button
          className="ml-auto px-1 rounded-sm hover:bg-fail-subtle"
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
        className="shrink-0 flex gap-1 px-2 pt-1.5 border-b border-edge overflow-x-auto overflow-y-hidden"
        role="tablist"
      >
        {tabBtn("build", "Build", cellCount)}
        {tabBtn("equipment", "Equipment")}
        {tabBtn("systems", "Systems", systemCount)}
        {s.selectedDetailing !== "none" && tabBtn("detailing", "Detailing")}
        {/* "Output", not "Tools": the tools moved to the toolbar and the menu bar, and
            what is left is what they produced — proposals, summaries, the compile log. */}
        {tabBtn("tools", "Output")}
      </div>

      {/* ── scrollable body ── */}
      <div className="flex-1 overflow-y-auto p-2.5 min-h-0">
        {/* BUILD */}
        <div className={tab === "build" ? "flex flex-col gap-2" : "hidden"}>
          <BuildTab />
        </div>

        {/* EQUIPMENT — the per-scope equipment catalog, inline (browse / select
            / manage). Mounted only while active so its WebGL preview and fetch
            spin up on demand. */}
        <div className={tab === "equipment" ? "block" : "hidden"}>
          {tab === "equipment" && (
            <React.Suspense
              fallback={<p className="text-content-subtle">Loading catalog…</p>}
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
          <div className="mt-3 pt-2 border-t border-edge">
            <div className="font-semibold text-content mb-1.5">
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
        <div className={tab === "tools" ? "flex flex-col gap-2" : "hidden"}>
          <ToolsTab />
        </div>
      </div>

      {/* ── error / conflict banners (visible on any tab) ── */}
      {(s.conflict || compileState?.status === "error") && (
        <div className="px-2.5 py-1 border-t border-edge">
          {s.conflict && <p className="text-fail">{s.conflict}</p>}
          {compileState?.status === "error" && (
            <p className="text-fail">
              Compile failed: {compileState.error}
            </p>
          )}
        </div>
      )}

      {/* ── pinned footer ── */}
      <div className="flex items-center gap-2 px-2.5 py-2 border-t border-edge">
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
        <span className="ml-auto text-content-muted whitespace-nowrap">
          r{s.active.revision}
        </span>
      </div>
    </div>
  );
};

export default CellBuilderPanel;
