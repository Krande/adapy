// The split grid of the classic preset: panel groups around the viewport, in
// nested rows and columns with dividers between them (`../splitTree`).
//
// THE CANVAS IS NEVER MOVED. The classic frame renders the viewport once, in
// the same element it always had; the grid only draws a placeholder for the
// viewport leaf, and the frame places the viewport element over the
// placeholder's rect (`onViewportRect`). Splitting, resizing and rearranging
// move the placeholder; the canvas element stays where it is in the page and
// only changes size.
//
// A group shows its panels as tabs (one panel: its title), the shown one's
// frameless content below, with Float / Move panel / Close for it. A tab drags
// out (past `DRAG_START`): every group and the viewport show drop targets -- an
// edge splits the node there, a group's middle or its tab strip stacks the
// panel in it -- with the area the panel would take highlighted; a drop on no
// target floats the panel at the pointer. Dividers resize by pointer or arrow
// keys within each side's least size; a double-click shares the room evenly.

import React, {Suspense, useLayoutEffect, useRef, useState} from "react";
import {createPortal} from "react-dom";
import {useStore} from "zustand";
import {createStore} from "zustand/vanilla";

import ErrorBoundary from "@/components/common/ErrorBoundary";
import {
  AddGlyph,
  CloseGlyph,
  FloatGlyph,
  MinimiseGlyph,
  PinGlyph,
  WindowHeaderButton,
  WindowHeaderMenuButton,
} from "@/components/ui/PanelFrame";
import {GROUP_DIVIDER, GROUP_HEADER, groupTabClass, panelBodyAttrs} from "@/components/ui/panelChrome";

/** A collapsed group's strip: the tab strip's surface. */
const GROUP_HEADER_SURFACE = "bg-[var(--ada-window-header-bg,#1f2937)]";

import {
  DROP_ATTR,
  DROP_AXIS_ATTR,
  DROP_EDGES_ATTR,
  DROP_INDEX_ATTR,
  cancelDockDrag,
  dockDragStore,
  endDockDragAt,
  moveDockDrag,
  type DropEdge,
} from "../dockDrag";
import type {LayoutDoc} from "../layout";
import {PanelMount} from "../LayoutHost";
import type {PanelAction, PanelId, PanelProps, RegisteredPanel} from "../panel";
import {updateGrid} from "../reducers";
import {getPanel, isPanelAvailable, listPanels} from "../registry";
import {hide, reveal} from "../reveal";
import {offeredInMode} from "../shellModes";
import {
  COLLAPSED_PX,
  VIEWPORT_NODE,
  clampFixed,
  resizePair,
  setActiveTab,
  setFixedPx,
  setGroupCollapsed,
  setSplitSizes,
  splitShare,
  type GroupNode,
  type Rect,
  type ShownNode,
  type ShownSplit,
} from "../splitTree";
import {PANEL_ACTIONS_ATTR, ZONE_ATTR, ZONE_PANEL_ATTR} from "../zoneKeys";
import {
  GRID_GROUP_ATTR,
  GRID_ROOT_ATTR,
  GRID_TAB_ATTR,
  canDock,
  canFloat,
  classicGroupChrome,
  dockAt,
  dropPanel,
  floatOut,
  gridZone,
  groupLabel,
  homeOf,
  moveAction,
  popoutActions,
  togglePin,
  updateClassic,
  usePinnedPanels,
} from "./classicPlace";

/** The least size of a group and of the viewport, in px. */
const MIN_GROUP = {w: 160, h: 96};
const MIN_VIEWPORT = {w: 200, h: 120};
/** A divider's thickness, in px. */
const DIVIDER = 4;
/** An arrow key's step on a divider, in px. */
const KEY_STEP = 16;
/** The pointer travel that turns a press on a tab into a drag. */
const DRAG_START = 5;

// ---------------------------------------------------------------------------
// Dragging a docked panel by its tab.
// ---------------------------------------------------------------------------

/** Which panel a tab drag carries, and where the pointer is: drawn by `DragGhost`. */
const ghostStore = createStore<{title: string; x: number; y: number} | null>()(() => null);

/**
 * Dragging a panel by its tab: past `DRAG_START` the drop targets show and a
 * ghost follows the pointer; the drop docks it where the pointer is, or floats
 * it there when on no target. A press that never travelled stays a click.
 */
function useTabDrag(panel: RegisteredPanel) {
  const press = useRef<{x: number; y: number; on: boolean} | null>(null);
  const dragged = useRef(false);
  const reset = () => {
    press.current = null;
    ghostStore.setState(null, true);
  };
  return {
    onPointerDown: (e: React.PointerEvent) => {
      if (e.button !== 0) return;
      press.current = {x: e.clientX, y: e.clientY, on: false};
      dragged.current = false;
      // Held from the press: the pointer leaves a small tab before it has travelled far enough to drag.
      e.currentTarget.setPointerCapture?.(e.pointerId);
    },
    onPointerMove: (e: React.PointerEvent) => {
      const p = press.current;
      if (!p) return;
      if (!p.on) {
        if (Math.hypot(e.clientX - p.x, e.clientY - p.y) < DRAG_START) return;
        p.on = dragged.current = true;
      }
      ghostStore.setState({title: panel.title, x: e.clientX, y: e.clientY}, true);
      moveDockDrag(panel.id, e.clientX, e.clientY);
    },
    onPointerUp: (e: React.PointerEvent) => {
      const on = press.current?.on;
      reset();
      e.currentTarget.releasePointerCapture?.(e.pointerId);
      if (!on) return;
      const target = endDockDragAt(e.clientX, e.clientY);
      if (dropPanel(panel.id, target)) return;
      if (!target && (canFloat(panel) || homeOf(panel) === "window")) floatOut(panel.id, {x: e.clientX, y: e.clientY});
    },
    onPointerCancel: () => {
      if (press.current?.on) cancelDockDrag();
      reset();
    },
    // The click that ends a drag is not a tab's selection.
    onClickCapture: (e: React.MouseEvent) => {
      if (!dragged.current) return;
      dragged.current = false;
      e.preventDefault();
      e.stopPropagation();
    },
  };
}

/** The dragged panel's title at the pointer. */
export function DragGhost() {
  const g = useStore(ghostStore);
  if (!g) return null;
  return createPortal(
    <div
      aria-hidden="true"
      className="pointer-events-none fixed z-[80] rounded-sm border border-blue-400 bg-gray-800/90 px-2 py-1 text-xs text-white shadow-lg"
      // Left of the pointer near the right edge, so it stays on screen.
      style={g.x > window.innerWidth - 200 ? {right: window.innerWidth - g.x + 12, top: g.y + 12} : {left: g.x + 12, top: g.y + 12}}
    >
      {g.title}
    </div>,
    document.body,
  );
}

/** Is a panel the grid may take being dragged. */
function useDraggingDockable(): boolean {
  return useStore(dockDragStore, (s) => {
    const p = s.panel ? getPanel(s.panel) : undefined;
    return !!p && canDock(p);
  });
}

// ---------------------------------------------------------------------------
// Drop targets.
// ---------------------------------------------------------------------------

/** The part of a node a drop at `edge` gives the panel, as percentages of the node. */
export function previewArea(edge: DropEdge, share: number): {left: string; top: string; width: string; height: string} {
  const pct = (v: number) => `${Math.round(v * 1000) / 10}%`;
  switch (edge) {
    case "left":
      return {left: "0%", top: "0%", width: pct(share), height: "100%"};
    case "right":
      return {left: pct(1 - share), top: "0%", width: pct(share), height: "100%"};
    case "top":
      return {left: "0%", top: "0%", width: "100%", height: pct(share)};
    case "bottom":
      return {left: "0%", top: pct(1 - share), width: "100%", height: pct(share)};
    default:
      return {left: "0%", top: "0%", width: "100%", height: "100%"};
  }
}

/** The edge of `zone` a drop would take while the pointer is over it; null when it is not. */
function useDropEdge(zone: string): DropEdge | null {
  return useStore(dockDragStore, (s) => (s.over === zone && s.index === null ? (s.edge ?? null) : null));
}

/** A node's outline while a drag is on, and the area the panel would take while the pointer is over it. */
function DropPreview({zone, share}: {zone: string; share: number}) {
  const edge = useDropEdge(zone);
  return (
    <div aria-hidden="true" className="pointer-events-none absolute inset-0 z-20 rounded-sm border-2 border-dashed border-gray-400/60">
      {edge && <div className="absolute rounded-sm border-2 border-blue-400 bg-blue-500/30" style={previewArea(edge, edge === "center" ? 1 : share)} />}
    </div>
  );
}

const dropAttrs = (zone: string, more: Record<string, string> = {}) => ({[DROP_ATTR]: zone, ...more});

/**
 * While a dockable panel is dragged: the viewport's drop target, over the
 * viewport element (the canvas is not a DOM node the drop test can mark). Its
 * edges split the viewport.
 */
export function ViewportDropTarget({viewport}: {viewport: React.RefObject<HTMLElement | null>}) {
  const dragging = useDraggingDockable();
  const [rect, setRect] = useState<Rect | null>(null);
  useLayoutEffect(() => {
    if (!dragging) return setRect(null);
    const r = viewport.current?.getBoundingClientRect();
    setRect(r && r.width > 0 && r.height > 0 ? {left: r.left, top: r.top, width: r.width, height: r.height} : null);
  }, [dragging]);
  if (!dragging || !rect) return null;
  const zone = gridZone(VIEWPORT_NODE);
  return createPortal(
    <div {...dropAttrs(zone, {[DROP_EDGES_ATTR]: "edges"})} className="fixed z-[43]" style={rect} aria-hidden="true">
      <DropPreview zone={zone} share={splitShare(VIEWPORT_NODE)} />
    </div>,
    document.body,
  );
}

// ---------------------------------------------------------------------------
// The grid.
// ---------------------------------------------------------------------------

export interface GridViewProps {
  /** The grid as drawn (`shownGrid`). */
  grid: ShownNode;
  doc: LayoutDoc;
  /** Components the preset provides synchronously, by panel id. */
  contents?: Readonly<Record<PanelId, React.ComponentType<PanelProps>>>;
  /** The element the viewport is positioned in. */
  frame: React.RefObject<HTMLElement | null>;
  /** The viewport leaf's rect within `frame`, whenever it may have moved. */
  onViewportRect(rect: Rect): void;
  /** Room left free on the left, in px: the tree drawer at home, which overlays the frame. */
  leftInset?: number;
}

/** The split grid, the size of the viewport's place in the frame. */
export function GridView({grid, doc, contents, frame, onViewportRect, leftInset = 0}: GridViewProps) {
  return (
    <div
      className="relative flex w-full h-full min-w-0 min-h-0"
      style={leftInset > 0 ? {paddingLeft: leftInset} : undefined}
      {...{[GRID_ROOT_ATTR]: ""}}
    >
      <GridNodeView node={grid} doc={doc} contents={contents} frame={frame} onViewportRect={onViewportRect} />
      <DragGhost />
    </div>
  );
}

/** `along`: the direction of the split the node sits in (a collapsed group's strip runs across it). */
type NodeProps = Omit<GridViewProps, "grid"> & {node: ShownNode; along?: "row" | "col"};

function GridNodeView(props: NodeProps) {
  const {node} = props;
  if (node.kind === "viewport") return <ViewportSlot frame={props.frame} onViewportRect={props.onViewportRect} />;
  if (node.kind === "group") return <GridGroup group={node} doc={props.doc} contents={props.contents} along={props.along ?? "row"} />;
  return <GridSplit {...props} split={node} />;
}

const flexOf = (size: number): React.CSSProperties => ({flex: `${size} 1 0px`});

/** A node's size in px when it is held at one (collapsed or fixed), else null. */
function pxOf(node: ShownNode, livePx?: number): number | null {
  if (node.kind === "group" && node.collapsed) return COLLAPSED_PX;
  if (node.kind !== "viewport" && node.fixed) return livePx ?? node.fixed.px;
  return null;
}

/** The least size of a drawn node along `dir`, in px: its fixed size, else the least of its parts and panels. */
function minAlong(node: ShownNode, dir: "row" | "col"): number {
  const w = dir === "row";
  const px = pxOf(node);
  if (px !== null) return px;
  if (node.kind === "viewport") return w ? MIN_VIEWPORT.w : MIN_VIEWPORT.h;
  if (node.kind === "group") {
    // A panel's own least size (`PanelConstraints.minSize`) counts too.
    const own = node.panels.map((id) => getPanel(id)?.constraints?.minSize?.[w ? "w" : "h"] ?? 0);
    return Math.max(w ? MIN_GROUP.w : MIN_GROUP.h, ...own);
  }
  if (node.dir === dir) return node.children.reduce((a, c) => a + minAlong(c, dir), 0) + DIVIDER * (node.children.length - 1);
  return Math.max(...node.children.map((c) => minAlong(c, dir)));
}

function GridSplit(props: NodeProps & {split: ShownSplit}) {
  const {split} = props;
  const ref = useRef<HTMLDivElement>(null);
  // While a divider is dragged, the shown sizes; stored on release.
  const [live, setLive] = useState<number[] | null>(null);
  // ...and, while it resizes a fixed child, that child's px.
  const [livePx, setLivePx] = useState<{id: string; px: number} | null>(null);
  const sizes = live ?? split.sizes;
  const row = split.dir === "row";
  const fixedChild = (c: ShownNode) => (c.kind !== "viewport" && c.fixed && !(c.kind === "group" && c.collapsed) ? c : null);
  const collapsed = (c: ShownNode) => c.kind === "group" && !!c.collapsed;
  /** The divider after child `i` resizes a fixed neighbour in px (`sign` +1: the one before it). */
  const fixedAt = (i: number): {node: NonNullable<ReturnType<typeof fixedChild>>; sign: 1 | -1} | null => {
    const a = fixedChild(split.children[i]);
    if (a) return {node: a, sign: 1};
    const b = fixedChild(split.children[i + 1]);
    return b ? {node: b, sign: -1} : null;
  };
  const resizeFixed = (i: number, px: number, done: boolean) => {
    const f = fixedAt(i)!;
    const next = clampFixed(f.node.fixed!, f.node.fixed!.px + f.sign * px);
    if (!done) return setLivePx({id: f.node.id, px: next});
    setLivePx(null);
    updateClassic((d) => updateGrid(d, (g) => setFixedPx(g, f.node.id, next)));
  };

  /** The stored sizes with the shown ones written back into their slots. */
  const stored = (shown: readonly number[]): number[] => {
    const all = [...(findStored(props.doc, split.id) ?? split.sizes)];
    const sum = split.slots.reduce((a, s) => a + (all[s] ?? 0), 0) || 1;
    split.slots.forEach((s, i) => (all[s] = shown[i] * sum));
    return all;
  };
  const commit = (shown: readonly number[]) => updateClassic((d) => updateGrid(d, (g) => setSplitSizes(g, split.id, stored(shown))));
  /** The shown sizes after moving the boundary after child `i` by `px`. */
  const moved = (from: readonly number[], i: number, px: number): number[] => {
    const box = ref.current?.getBoundingClientRect();
    const room = (row ? box?.width : box?.height) ?? 0;
    // Children held at a px size take that out of the room the fractions share.
    const held = split.children.reduce((a, c) => a + (pxOf(c) ?? 0), 0);
    const free = room - held - DIVIDER * (split.children.length - 1);
    if (free <= 0) return [...from];
    const mins = split.children.map((c) => minAlong(c, split.dir) / free);
    return resizePair(from, i, i + 1, px / free, mins);
  };
  // The shown children share the room evenly; hidden ones keep theirs.
  const equalise = () => {
    setLive(null);
    commit(split.children.map(() => 1 / split.children.length));
  };

  // The children not held at a px size share what is left, by their fractions scaled to 1:
  // flex-grow values summing below 1 would hand out only that part of the room.
  const fracSum = split.children.reduce((a, c, i) => a + (pxOf(c) === null ? sizes[i] : 0), 0) || 1;
  const kids: React.ReactNode[] = [];
  split.children.forEach((c, i) => {
    if (i > 0) {
      const prev = split.children[i - 1];
      const fixed = fixedAt(i - 1);
      // Beside a collapsed strip there is nothing to resize: a plain rule.
      if (collapsed(prev) || collapsed(c)) {
        kids.push(<div key={`d${i}`} aria-hidden="true" className={`shrink-0 ${GROUP_DIVIDER} ${row ? "w-1" : "h-1"}`} />);
      } else if (fixed) {
        kids.push(
          <Divider
            key={`d${i}`}
            dir={split.dir}
            label={`Resize ${nodeName(fixed.node)}`}
            value={Math.round(livePx?.id === fixed.node.id ? livePx.px : fixed.node.fixed!.px)}
            onDrag={(px) => resizeFixed(i - 1, px, false)}
            onDragEnd={(px) => resizeFixed(i - 1, px, true)}
            onStep={(px) => resizeFixed(i - 1, px, true)}
            onEqualise={() => {}}
          />,
        );
      } else {
        kids.push(
          <Divider
            key={`d${i}`}
            dir={split.dir}
            label={`Resize between ${nodeName(prev)} and ${nodeName(c)}`}
            value={Math.round(sizes.slice(0, i).reduce((a, b) => a + b, 0) * 100)}
            onDrag={(px) => setLive(moved(split.sizes, i - 1, px))}
            onDragEnd={(px) => {
              const next = moved(split.sizes, i - 1, px);
              setLive(null);
              commit(next);
            }}
            onStep={(px) => commit(moved(split.sizes, i - 1, px))}
            onEqualise={equalise}
          />,
        );
      }
    }
    const px = pxOf(c, c.kind !== "viewport" && livePx?.id === c.id ? livePx.px : undefined);
    kids.push(
      <div
        key={nodeKey(c)}
        className={`relative flex min-w-0 min-h-0 ${row ? "h-full" : "w-full"}`}
        style={px !== null ? {flex: `0 0 ${px}px`} : flexOf(sizes[i] / fracSum)}
      >
        <GridNodeView {...props} node={c} along={split.dir} />
      </div>,
    );
  });
  return (
    <div ref={ref} className={`flex ${row ? "flex-row" : "flex-col"} w-full h-full min-w-0 min-h-0`} data-grid-split={split.id}>
      {kids}
    </div>
  );
}

function findStored(doc: LayoutDoc, id: string): number[] | null {
  const visit = (n: NonNullable<LayoutDoc["grid"]>): number[] | null => {
    if (n.kind !== "split") return null;
    if (n.id === id) return n.sizes;
    for (const c of n.children) {
      const hit = visit(c);
      if (hit) return hit;
    }
    return null;
  };
  return doc.grid ? visit(doc.grid) : null;
}

const nodeKey = (n: ShownNode) => (n.kind === "viewport" ? VIEWPORT_NODE : n.id);

/** A node's name for a divider's label. */
function nodeName(n: ShownNode): string {
  if (n.kind === "viewport") return "the view";
  if (n.kind === "group") return groupLabel(n.panels);
  return "panels";
}

function Divider({
  dir,
  label,
  value,
  onDrag,
  onDragEnd,
  onStep,
  onEqualise,
}: {
  dir: "row" | "col";
  label: string;
  value: number;
  onDrag(px: number): void;
  onDragEnd(px: number): void;
  onStep(px: number): void;
  onEqualise(): void;
}) {
  const start = useRef<{x: number; y: number} | null>(null);
  const row = dir === "row";
  const offset = (e: React.PointerEvent) => (start.current ? (row ? e.clientX - start.current.x : e.clientY - start.current.y) : 0);
  return (
    <div
      role="separator"
      aria-orientation={row ? "vertical" : "horizontal"}
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={value}
      tabIndex={0}
      className={`group relative shrink-0 touch-none ${GROUP_DIVIDER} focus:outline-none ${row ? "w-1 cursor-ew-resize" : "h-1 cursor-ns-resize"}`}
      onPointerDown={(e) => {
        if (e.button !== 0) return;
        e.preventDefault();
        start.current = {x: e.clientX, y: e.clientY};
        e.currentTarget.setPointerCapture(e.pointerId);
      }}
      onPointerMove={(e) => {
        if (start.current) onDrag(offset(e));
      }}
      onPointerUp={(e) => {
        if (!start.current) return;
        const px = offset(e);
        start.current = null;
        e.currentTarget.releasePointerCapture?.(e.pointerId);
        onDragEnd(px);
      }}
      onPointerCancel={() => {
        start.current = null;
        onDrag(0);
      }}
      onDoubleClick={onEqualise}
      onKeyDown={(e) => {
        const back = row ? "ArrowLeft" : "ArrowUp";
        const fwd = row ? "ArrowRight" : "ArrowDown";
        if (e.key === back) onStep(-KEY_STEP);
        else if (e.key === fwd) onStep(KEY_STEP);
        else return;
        e.preventDefault();
      }}
    >
      {/* A wider grab area than the line it draws. */}
      <span className={`absolute z-10 ${row ? "inset-y-0 -left-1 -right-1" : "inset-x-0 -top-1 -bottom-1"}`} />
      <span
        aria-hidden="true"
        className="absolute inset-0 bg-transparent group-hover:bg-blue-400/70 group-focus-visible:bg-blue-400"
      />
    </div>
  );
}

/** Where the viewport goes: an empty slot whose rect the frame puts the viewport over. */
function ViewportSlot({frame, onViewportRect}: Pick<GridViewProps, "frame" | "onViewportRect">) {
  const ref = useRef<HTMLDivElement>(null);
  const report = useRef(onViewportRect);
  report.current = onViewportRect;
  const measure = () => {
    const el = ref.current;
    const f = frame.current;
    if (!el || !f) return;
    const r = el.getBoundingClientRect();
    const o = f.getBoundingClientRect();
    report.current({left: r.left - o.left, top: r.top - o.top, width: r.width, height: r.height});
  };
  // Every render may have moved it; a resize of the slot or the frame, or of the window, too.
  useLayoutEffect(measure);
  useLayoutEffect(() => {
    const ro = typeof ResizeObserver !== "undefined" ? new ResizeObserver(measure) : null;
    if (ref.current) ro?.observe(ref.current);
    if (frame.current) ro?.observe(frame.current);
    window.addEventListener("resize", measure);
    return () => {
      ro?.disconnect();
      window.removeEventListener("resize", measure);
    };
  }, []);
  return <div ref={ref} className="w-full h-full min-w-0 min-h-0" data-grid-viewport="" aria-hidden="true" />;
}

// ---------------------------------------------------------------------------
// A group.
// ---------------------------------------------------------------------------

/** An action's title-bar button: a menu button when it has a menu. */
function actionButton(a: PanelAction) {
  if (a.menu) return <WindowHeaderMenuButton key={a.id} action={a} />;
  return (
    <WindowHeaderButton key={a.id} onClick={a.run} title={a.label} ariaLabel={a.label} disabled={a.disabled} pressed={a.pressed}>
      {a.icon ? <a.icon /> : a.label}
    </WindowHeaderButton>
  );
}

/** The insertion mark before (or, `after`, behind) a tab. */
const InsertMark: React.FC<{after?: boolean}> = ({after}) => (
  <span aria-hidden="true" className={`pointer-events-none absolute inset-y-0 z-10 w-0.5 bg-blue-400 ${after ? "right-0" : "left-0"}`} />
);

function GroupTab({
  panel,
  group,
  index,
  selected,
  dragging,
  mark,
  icons = false,
}: {
  panel: RegisteredPanel;
  group: GroupNode;
  index: number;
  selected: boolean;
  dragging: boolean;
  mark: "before" | "after" | null;
  icons?: boolean;
}) {
  const drag = useTabDrag(panel);
  return (
    <button
      type="button"
      role="tab"
      id={`grid-tab-${panel.id}`}
      aria-selected={selected}
      aria-controls={`grid-panel-${panel.id}`}
      tabIndex={selected ? 0 : -1}
      {...{[GRID_TAB_ATTR]: panel.id, [ZONE_PANEL_ATTR]: panel.id}}
      {...(dragging ? dropAttrs(gridZone(group.id), {[DROP_INDEX_ATTR]: String(index), [DROP_AXIS_ATTR]: "x"}) : {})}
      className={`relative shrink-0 max-w-[14rem] truncate select-none rounded-sm px-2 py-1 text-xs cursor-grab ${groupTabClass(selected)}`}
      title={panel.title}
      onClick={() => updateClassic((d) => updateGrid(d, (g) => setActiveTab(g, panel.id)))}
      onKeyDown={(e) => {
        // Arrows between the tabs of the strip.
        if (e.ctrlKey || e.altKey || e.metaKey || (e.key !== "ArrowLeft" && e.key !== "ArrowRight")) return;
        const tabs = Array.from(e.currentTarget.parentElement?.querySelectorAll<HTMLElement>('[role="tab"]') ?? []);
        const i = tabs.indexOf(e.currentTarget);
        const next = tabs[(i + (e.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length];
        if (!next || next === e.currentTarget) return;
        e.preventDefault();
        next.focus();
        next.click();
      }}
      {...drag}
    >
      {tabLabel(panel, icons)}
      {mark && <InsertMark after={mark === "after"} />}
    </button>
  );
}

/** A panel's own actions in a group: float, move, pop out, pin (when the shell asks). */
function panelActions(p: RegisteredPanel, doc: LayoutDoc, pinned: readonly PanelId[], chrome: ReturnType<typeof classicGroupChrome>): PanelAction[] {
  const on = pinned.includes(p.id);
  return [
    ...(canFloat(p) || homeOf(p) === "window" ? [{id: "float", label: "Float in a window", icon: FloatGlyph, run: () => floatOut(p.id)}] : []),
    moveAction(p, doc),
    ...popoutActions(p),
    ...(chrome.actions.includes("pin") && p.capabilities.pinnable
      ? [{id: "pin", label: on ? "Unpin: leave with the mode" : "Pin: keep across modes", icon: PinGlyph, pressed: on, run: () => togglePin(p.id)}]
      : []),
  ];
}

/** The group's own actions (when the shell asks): add a panel, collapse to a strip. */
function groupActions(group: GroupNode, chrome: ReturnType<typeof classicGroupChrome>): PanelAction[] {
  const out: PanelAction[] = [];
  if (chrome.actions.includes("add")) {
    const addable = listPanels()
      .filter((p) => !group.panels.includes(p.id) && canDock(p) && offeredInMode(p) && isPanelAvailable(p, {}))
      .sort((a, b) => a.title.localeCompare(b.title));
    out.push({
      id: "add",
      label: "Add a panel here",
      icon: AddGlyph,
      disabled: addable.length === 0,
      run: () => {},
      menu: addable.map((p) => ({
        id: `add-${p.id}`,
        label: p.title,
        run: () => {
          dockAt(p.id, {node: group.id, edge: "center"});
          void reveal(p.id);
        },
      })),
    });
  }
  if (chrome.actions.includes("collapse")) {
    out.push({id: "collapse", label: "Collapse to a strip", icon: MinimiseGlyph, run: () => updateClassic((d) => updateGrid(d, (g) => setGroupCollapsed(g, group.id, true)))});
  }
  return out;
}

/** A collapsed group: a strip of its panels' icons across its split; one restores the group on that panel. */
function GroupStrip({group, panels, along}: {group: GroupNode; panels: RegisteredPanel[]; along: "row" | "col"}) {
  const restore = (id: PanelId) =>
    updateClassic((d) => updateGrid(d, (g) => setActiveTab(setGroupCollapsed(g, group.id, false), id)));
  return (
    <section
      aria-label={`${groupLabel(group.panels)} (collapsed)`}
      {...{[GRID_GROUP_ATTR]: group.id}}
      className={`flex ${along === "row" ? "flex-col" : "flex-row"} items-center gap-1 w-full h-full p-0.5 overflow-hidden ${GROUP_HEADER_SURFACE}`}
    >
      {panels.map((p) => (
        <button
          key={p.id}
          type="button"
          className={`inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-sm text-xs ${groupTabClass(p.id === group.active)}`}
          title={`${p.title}: expand`}
          aria-label={`Expand ${p.title}`}
          onClick={() => restore(p.id)}
        >
          <span aria-hidden="true" className="inline-flex [&_svg]:h-4 [&_svg]:w-4">
            {p.icon ? <p.icon /> : p.title.slice(0, 1)}
          </span>
        </button>
      ))}
    </section>
  );
}

/** Below this height per panel, an `"auto"` group shows tabs rather than stacking. */
const STACK_MIN_PX = 160;

/** An element's height, measured while it is mounted: a callback ref, so a remounted element is measured too. */
function useHeight(): [number, (el: HTMLElement | null) => void] {
  const [h, setH] = useState(0);
  const ro = useRef<ResizeObserver | null>(null);
  const attach = React.useCallback((el: HTMLElement | null) => {
    ro.current?.disconnect();
    ro.current = null;
    if (!el) return;
    const read = () => setH(el.getBoundingClientRect().height);
    read();
    if (typeof ResizeObserver !== "undefined") {
      ro.current = new ResizeObserver(read);
      ro.current.observe(el);
    }
  }, []);
  return [h, attach];
}

/** A panel's tab label: its icon (when the shell asks) and title. */
function tabLabel(p: RegisteredPanel, icons: boolean): React.ReactNode {
  if (!icons || !p.icon) return p.title;
  const Icon = p.icon;
  return (
    <span className="inline-flex items-center gap-1.5">
      <span aria-hidden="true" className="inline-flex h-4 w-4 shrink-0 items-center justify-center [&_svg]:h-4 [&_svg]:w-4">
        <Icon />
      </span>
      <span className="truncate">{p.title}</span>
    </span>
  );
}

/** A panel's body in its group: padded and scrolled by the host, or edge to edge (`capabilities.fill`). */
const bodyClass = (p: RegisteredPanel) => (p.capabilities.fill ? "overflow-hidden" : "overflow-auto p-2");

function GridGroup({
  group,
  doc,
  contents,
  along,
}: {
  group: GroupNode;
  doc: LayoutDoc;
  contents?: Readonly<Record<PanelId, React.ComponentType<PanelProps>>>;
  along: "row" | "col";
}) {
  const panels = group.panels.map((id) => getPanel(id)).filter((p): p is RegisteredPanel => !!p);
  const active = panels.find((p) => p.id === group.active) ?? panels[0];
  const dragging = useDraggingDockable();
  const zone = gridZone(group.id);
  const hover = useStore(dockDragStore, (s) => (s.over === zone && s.index !== null ? s.index : null));
  const pinned = usePinnedPanels();
  const chrome = classicGroupChrome();
  const [height, measure] = useHeight();
  const markAt = (i: number, n: number): "before" | "after" | null =>
    hover === null ? null : hover === i ? "before" : hover >= n && i === n - 1 ? "after" : null;
  if (!active) return null;
  if (group.collapsed) return <GroupStrip group={group} panels={panels} along={along} />;
  const arrangement = group.arrangement ?? "tabs";
  const stacked =
    panels.length > 1 &&
    panels.every((p) => p.capabilities.stackable) &&
    (arrangement === "stack" || (arrangement === "auto" && height >= panels.length * STACK_MIN_PX));
  if (stacked) {
    return <StackedGroup group={group} panels={panels} doc={doc} contents={contents} pinned={pinned} chrome={chrome} dragging={dragging} sectionRef={measure} />;
  }
  const close = () => void hide(active.id);
  const actions: PanelAction[] = [...panelActions(active, doc, pinned, chrome), ...groupActions(group, chrome)];
  return (
    <section
      ref={measure}
      aria-label={groupLabel(group.panels)}
      {...{[ZONE_ATTR]: "dock", [GRID_GROUP_ATTR]: group.id}}
      {...(dragging ? dropAttrs(zone, {[DROP_EDGES_ATTR]: "all"}) : {})}
      className={GROUP_SECTION}
    >
      <div className={GROUP_HEADER}>
        <div
          role="tablist"
          aria-label={groupLabel(group.panels)}
          {...(dragging ? dropAttrs(zone, {[DROP_INDEX_ATTR]: String(panels.length)}) : {})}
          className="flex min-w-0 flex-1 self-stretch items-center gap-0.5 overflow-x-auto"
        >
          {panels.map((p, i) => (
            <GroupTab
              key={p.id}
              panel={p}
              group={group}
              index={i}
              selected={p.id === active.id}
              dragging={dragging}
              mark={markAt(i, panels.length)}
              icons={chrome.tabIcons}
            />
          ))}
        </div>
        <div className="flex items-center gap-1" {...{[PANEL_ACTIONS_ATTR]: active.id}}>
          {actions.map(actionButton)}
          {active.capabilities.closable && (
            <WindowHeaderButton onClick={close} title="Close" ariaLabel="Close panel">
              <CloseGlyph />
            </WindowHeaderButton>
          )}
        </div>
      </div>
      <div className="relative flex-1 min-h-0">
        {panels.map((p) => (
          <div
            key={p.id}
            role="tabpanel"
            id={`grid-panel-${p.id}`}
            aria-labelledby={`grid-tab-${p.id}`}
            hidden={p.id !== active.id}
            {...{[ZONE_PANEL_ATTR]: p.id}}
            className={`absolute inset-0 ${bodyClass(p)}`}
            {...panelBodyAttrs}
          >
            <GroupPanel panel={p} contents={contents} active={p.id === active.id} />
          </div>
        ))}
      </div>
      {dragging && <DropPreview zone={zone} share={splitShare(group.id)} />}
    </section>
  );
}

const GROUP_SECTION =
  "relative flex flex-col w-full h-full min-w-0 min-h-0 overflow-hidden bg-[var(--ada-panel-bg)] text-[var(--ada-panel-text)] text-sm";

/** A group showing every panel at once, each under a header of its own (title, its actions, close). */
function StackedGroup({
  group,
  panels,
  doc,
  contents,
  pinned,
  chrome,
  dragging,
  sectionRef,
}: {
  group: GroupNode;
  panels: RegisteredPanel[];
  doc: LayoutDoc;
  contents?: Readonly<Record<PanelId, React.ComponentType<PanelProps>>>;
  pinned: readonly PanelId[];
  chrome: ReturnType<typeof classicGroupChrome>;
  dragging: boolean;
  sectionRef: (el: HTMLElement | null) => void;
}) {
  const zone = gridZone(group.id);
  const own = groupActions(group, chrome);
  return (
    <section
      ref={sectionRef}
      aria-label={groupLabel(group.panels)}
      {...{[ZONE_ATTR]: "dock", [GRID_GROUP_ATTR]: group.id}}
      {...(dragging ? dropAttrs(zone, {[DROP_EDGES_ATTR]: "all"}) : {})}
      className={GROUP_SECTION}
    >
      {panels.map((p, i) => (
        <div key={p.id} className="flex min-h-0 flex-1 flex-col" {...{[ZONE_PANEL_ATTR]: p.id}}>
          <div className={GROUP_HEADER}>
            <div className="flex min-w-0 flex-1 items-center px-1 text-xs font-medium" {...{[GRID_TAB_ATTR]: p.id}} tabIndex={-1}>
              {tabLabel(p, chrome.tabIcons)}
            </div>
            <div className="flex items-center gap-1" {...{[PANEL_ACTIONS_ATTR]: p.id}}>
              {[...panelActions(p, doc, pinned, chrome), ...(i === 0 ? own : [])].map(actionButton)}
              {p.capabilities.closable && (
                <WindowHeaderButton onClick={() => void hide(p.id)} title="Close" ariaLabel={`Close ${p.title}`}>
                  <CloseGlyph />
                </WindowHeaderButton>
              )}
            </div>
          </div>
          <div className={`relative min-h-0 flex-1 ${bodyClass(p)}`} {...panelBodyAttrs}>
            <GroupPanel panel={p} contents={contents} active />
          </div>
        </div>
      ))}
      {dragging && <DropPreview zone={zone} share={splitShare(group.id)} />}
    </section>
  );
}

function GroupPanel({
  panel,
  contents,
  active,
}: {
  panel: RegisteredPanel;
  contents?: Readonly<Record<PanelId, React.ComponentType<PanelProps>>>;
  active: boolean;
}) {
  const props: PanelProps = {panelId: panel.id, host: {kind: "dock", zone: "classic.grid"}, close: () => void hide(panel.id), isActive: active};
  return (
    <ErrorBoundary label={panel.title}>
      <Suspense fallback={<div className="p-2 text-sm text-gray-400">Loading…</div>}>
        <PanelMount panel={panel} provided={contents?.[panel.id]} props={props} which="content" outerSuspense />
      </Suspense>
    </ErrorBoundary>
  );
}
