// Windows of the classic preset's layout, behind the "Customisable panels"
// setting (`LayoutPrefs.customisablePanels`, on unless turned off).
//
// With the setting off nothing here renders and no header gets an action, so the
// classic UI is unchanged. With it on:
//
//   * a column or tree-track panel that has frameless content and may float gets
//     a "Float" action in its own header (`PanelHeaderActions`);
//   * a floated panel shows in a window of its own, its content in the shared
//     `PanelFrame` with Back home / Minimise / Move panel / Close;
//   * a minimised one waits in the tray at the end of the top bar;
//   * a window dragged by its title bar drops into the split grid
//     (`./classicGrid`), or, for a column panel, back onto the panel column,
//     outlined as a drop target meanwhile.
//
// Core's panels keep their visibility flags: the classic document records only
// WHERE a panel shows (`floats`, `minimised`, `grid`), and the flag still says
// WHETHER. So a floated panel closed from the top bar reopens in its window.

import React, {useRef} from "react";
import {createPortal} from "react-dom";
import {useStore} from "zustand";

import {FloatingWindow} from "@/components/ui/FloatingWindow";
import {TransformedHost} from "@/components/ui/OverlayPortal";
import {DockGlyph, FloatGlyph, MinimiseGlyph, PanelFrame} from "@/components/ui/PanelFrame";
import {navBtnClass} from "@/components/navButton";

import {DROP_ATTR, dockDragStore, endDockDragAt, moveDockDrag} from "../dockDrag";
import {CLASSIC_ZONES, type LayoutDoc} from "../layout";
import {PanelMount, usePanelStates, useLayout} from "../LayoutHost";
import type {PanelAction, PanelEnv, PanelId, PanelProps, RegisteredPanel} from "../panel";
import type {PanelState} from "../panelStates";
import {minimisePanel, raiseFloat, restorePanel, setFloatRect} from "../reducers";
import {getPanel} from "../registry";
import {hide} from "../reveal";
import {ZoneKeys, requestFloatFocus, useFloatFocus} from "../zoneFocus";
import {ZONE_ATTR, ZONE_PANEL_ATTR} from "../zoneKeys";
import {
  FLOAT_MIN_SIZE,
  HOME_LABEL,
  backHome,
  canDock,
  canFloat,
  dropPanel,
  floatOut,
  homeOf,
  moveAction,
  popoutActions,
  updateClassic,
  useClassicDoc,
} from "./classicPlace";

const DRAG_HANDLE = "classic-undock-drag-handle";

/** The actions a column or tree-track panel's own header offers: Float, and the Move menu when it can dock. */
export function columnActions(panel: RegisteredPanel, doc?: LayoutDoc): PanelAction[] {
  const out: PanelAction[] = [];
  if (canFloat(panel)) {
    const run = () => {
      requestFloatFocus(panel.id);
      floatOut(panel.id);
    };
    out.push({id: "float", label: "Float in a window", icon: FloatGlyph, run});
  }
  if (doc && canDock(panel)) out.push(moveAction(panel, doc));
  out.push(...popoutActions(panel));
  return out;
}

const closeFloat = (id: PanelId) => void hide(id);

/** F6 between the classic zones, and Esc on a window. */
export function ClassicZoneKeys() {
  return <ZoneKeys closeFloat={closeFloat} />;
}

/** The actions a floated panel's window offers, before its close button. */
export function floatActions(panel: RegisteredPanel, doc: LayoutDoc): PanelAction[] {
  const home = homeOf(panel);
  return [
    ...(home === "column" || home === "tree" ? [{id: "dock", label: HOME_LABEL[home], icon: DockGlyph, run: () => backHome(panel.id)}] : []),
    {id: "minimise", label: "Minimise", icon: MinimiseGlyph, run: () => updateClassic((d) => minimisePanel(d, panel.id))},
    moveAction(panel, doc),
    ...popoutActions(panel),
  ];
}

/** Does the panel show in a window of the layout. */
export function isUndocked(doc: LayoutDoc, id: PanelId): boolean {
  return !!doc.floats[id];
}

function UndockedWindow({
  panel,
  doc,
  contents,
}: {
  panel: RegisteredPanel;
  doc: LayoutDoc;
  contents?: Readonly<Record<PanelId, React.ComponentType<PanelProps>>>;
}) {
  const f = doc.floats[panel.id]!;
  const ref = useRef<HTMLDivElement>(null);
  useFloatFocus(panel.id, ref);
  const close = () => void hide(panel.id);
  const props: PanelProps = {panelId: panel.id, host: {kind: "float", zone: CLASSIC_ZONES.float}, close, isActive: true};
  return (
    <FloatingWindow
      dragHandleClassName={DRAG_HANDLE}
      zIndex={45}
      zClassName="z-[45]"
      overlay
      fullScreenWhenNarrow
      minSize={FLOAT_MIN_SIZE}
      style={{zIndex: f.z}}
      rect={{x: f.x, y: f.y, width: f.w, height: f.h}}
      onRectChange={(r) => updateClassic((d) => setFloatRect(d, panel.id, {x: r.x, y: r.y, w: r.width, h: r.height}))}
      // Drag-to-dock: into the grid, or back onto the panel column.
      onDragMove={(p) => moveDockDrag(panel.id, p.x, p.y)}
      onDragEnd={(p) => dropPanel(panel.id, endDockDragAt(p.x, p.y))}
    >
      {({fullScreen}) => (
        <div
          ref={ref}
          className="h-full w-full"
          role="dialog"
          aria-modal="false"
          aria-label={panel.title}
          {...{[ZONE_ATTR]: "float", [ZONE_PANEL_ATTR]: panel.id}}
          onPointerDownCapture={() => updateClassic((d) => raiseFloat(d, panel.id))}
        >
          <PanelFrame
            panelId={panel.id}
            title={panel.title}
            host={props.host}
            actions={floatActions(panel, doc)}
            onClose={panel.capabilities.closable ? close : undefined}
            dragHandleClassName={fullScreen ? undefined : DRAG_HANDLE}
          >
            {/* The column card gave the content its text colour and padding; the window body does not. */}
            <div className={`h-full text-sm text-[var(--ada-panel-text)] ${panel.capabilities.fill ? "overflow-hidden" : "overflow-y-auto p-2"}`}>
              {/* The window moves by a transform: fixed overlays of the content portal out (`OverlayPortal`). */}
              <TransformedHost>
                <PanelMount panel={panel} provided={contents?.[panel.id]} props={props} which="content" outerSuspense />
              </TransformedHost>
            </div>
          </PanelFrame>
        </div>
      )}
    </FloatingWindow>
  );
}

/**
 * The floated panels' windows. `mounts` is the preset's rule for when a panel is
 * mounted at all; a floated panel also needs to be open, as its window is the
 * whole of it.
 */
export function UndockedLayer({
  panels,
  mounts,
  contents,
}: {
  panels: readonly RegisteredPanel[];
  mounts: (panel: RegisteredPanel, state: PanelState, env: PanelEnv) => boolean;
  contents?: Readonly<Record<PanelId, React.ComponentType<PanelProps>>>;
}) {
  const {env} = useLayout();
  const doc = useClassicDoc();
  const floated = panels.filter((p) => isUndocked(doc, p.id) && !doc.minimised.includes(p.id));
  const states = usePanelStates(floated, env);
  return (
    <>
      {floated.map((p) => {
        const s = states.get(p.id)!;
        if (!s.open || !mounts(p, s, env)) return null;
        return <UndockedWindow key={p.id} panel={p} doc={doc} contents={contents} />;
      })}
    </>
  );
}

/** The minimised panels, at the end of the top bar; a click brings one back. */
export function MinimisedTray() {
  const {env} = useLayout();
  const doc = useClassicDoc();
  const panels = doc.minimised.map((id) => getPanel(id)).filter((p): p is RegisteredPanel => !!p);
  const states = usePanelStates(panels, env);
  const shown = panels.filter((p) => states.get(p.id)?.open && states.get(p.id)?.available);
  if (shown.length === 0) return null;
  return (
    <div className="flex flex-row items-center gap-1 pl-2 border-l border-gray-600" role="group" aria-label="Minimised panels">
      {shown.map((p) => (
        <button
          key={p.id}
          type="button"
          className={navBtnClass(false)}
          onClick={() => {
            requestFloatFocus(p.id);
            updateClassic((d) => restorePanel(d, p.id));
          }}
          title={`Restore ${p.title}`}
          aria-label={`Restore ${p.title}`}
        >
          {p.icon ? <p.icon /> : <span className="text-xs px-1">{p.title}</span>}
        </button>
      ))}
    </div>
  );
}

/** The least size of the drop target, so an emptied column is still easy to hit. */
const DROP_MIN = {width: 320, height: 160};

/**
 * While a column panel is dragged out of its place: the panel column's drop
 * target, over the column, highlighted while the pointer is over it.
 */
export function ColumnDropTarget() {
  const dragging = useStore(dockDragStore, (s) => s.panel);
  const over = useStore(dockDragStore, (s) => s.over === CLASSIC_ZONES.menuColumn);
  const [rect, setRect] = React.useState<{left: number; top: number; width: number; height: number} | null>(null);
  const columnPanel = !!dragging && homeOf(getPanel(dragging) ?? {}) === "column";
  React.useLayoutEffect(() => {
    if (!columnPanel) return setRect(null);
    const column = document.querySelector(`[${ZONE_ATTR}="column"]`);
    const r = column?.getBoundingClientRect();
    setRect(r ? {left: r.left, top: r.top, width: Math.max(r.width, DROP_MIN.width), height: Math.max(r.height, DROP_MIN.height)} : null);
  }, [columnPanel]);
  if (!columnPanel || !rect) return null;
  return createPortal(
    <div
      {...{[DROP_ATTR]: CLASSIC_ZONES.menuColumn}}
      className={`fixed z-[44] rounded-md border-2 border-dashed flex items-start justify-center pt-6 text-sm transition-colors ${
        over ? "border-blue-400 bg-blue-500/25 text-blue-100" : "border-gray-400/70 bg-gray-900/30 text-gray-300"
      }`}
      style={rect}
      aria-hidden="true"
    >
      {HOME_LABEL.column}
    </div>,
    document.body,
  );
}
