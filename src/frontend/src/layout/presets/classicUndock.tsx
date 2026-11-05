// Undocking in the classic preset, behind the "Customisable panels" setting
// (`LayoutPrefs.customisablePanels`, default off).
//
// With the setting off nothing here renders and no header gets an action, so the
// classic UI is unchanged. With it on:
//
//   * a menu-column panel that has frameless content and may float gets a
//     "Float" action in its own header (`PanelHeaderActions`);
//   * a floated panel leaves the column and shows in a window of its own, its
//     content in the shared `PanelFrame` with Dock / Minimise / Close;
//   * a minimised one waits in the tray at the end of the top bar.
//
// Core's panels keep their visibility flags: the classic document records only
// WHERE a panel shows (`floats`, `minimised`), and the flag still says WHETHER.
// So a floated panel closed from the top bar reopens in its window.

import React, {useMemo} from "react";
import {useStore} from "zustand";

import {FloatingWindow} from "@/components/ui/FloatingWindow";
import {DockGlyph, FloatGlyph, MinimiseGlyph, PanelFrame} from "@/components/ui/PanelFrame";
import {navBtnClass} from "@/components/navButton";
import {defaultRect} from "@/assets/floatingWindow";

import {CLASSIC_PRESET_ID, layoutDocKey, type LayoutDoc} from "../layout";
import {PanelMount, usePanelStates, useLayout} from "../LayoutHost";
import {layoutStore} from "../layoutStore";
import type {PanelAction, PanelEnv, PanelId, PanelProps, RegisteredPanel} from "../panel";
import type {PanelState} from "../panelStates";
import {dockPanel, floatPanel, migrateLayoutDoc, minimisePanel, raiseFloat, restorePanel, setFloatRect} from "../reducers";
import {getPanel} from "../registry";
import {hide} from "../reveal";
import {CLASSIC_ZONE_DEFS} from "./classicRules";

const DRAG_HANDLE = "classic-undock-drag-handle";
const FIRST_SIZE = {w: 420, h: 560};
const MIN_SIZE = {width: 280, height: 160};

/** Is the "Customisable panels" setting on. */
export function useCustomisablePanels(): boolean {
  return useStore(layoutStore, (s) => s.prefs.customisablePanels === true);
}

/** The user's classic document, re-rendering when it changes. */
export function useClassicDoc(): LayoutDoc {
  const raw = useStore(layoutStore, (s) => s.prefs.docs[layoutDocKey(CLASSIC_PRESET_ID)]);
  return useMemo(() => migrateLayoutDoc(raw, CLASSIC_PRESET_ID, CLASSIC_ZONE_DEFS), [raw]);
}

function update(fn: (doc: LayoutDoc) => LayoutDoc): void {
  layoutStore.getState().update(CLASSIC_PRESET_ID, CLASSIC_ZONE_DEFS, fn);
}

/** A panel may leave the column: it allows floating and has content to put in a window. */
export function canFloat(panel: RegisteredPanel): boolean {
  return panel.capabilities.floatable && !!panel.content;
}

const CASCADE = 32;

/**
 * Float a panel where it last floated, else at its first-open size near the top
 * right, stepped down and left past the windows already open.
 */
export function floatColumnPanel(id: PanelId): void {
  const p = getPanel(id);
  const size = p?.placement?.[CLASSIC_PRESET_ID]?.float ?? FIRST_SIZE;
  const vw = typeof window !== "undefined" ? window.innerWidth : 1280;
  const vh = typeof window !== "undefined" ? window.innerHeight : 800;
  const r = defaultRect({width: vw, height: vh}, {width: size.w, height: size.h}, MIN_SIZE);
  update((d) => {
    const step = Object.keys(d.floats).length * CASCADE;
    return floatPanel(d, id, {x: Math.max(0, vw - r.width - 80 - step), y: r.y + step, w: r.width, h: r.height});
  });
}

/** The actions a column panel's header offers. */
export function columnActions(panel: RegisteredPanel): PanelAction[] {
  if (!canFloat(panel)) return [];
  return [{id: "float", label: "Float in a window", icon: FloatGlyph, run: () => floatColumnPanel(panel.id)}];
}

/** The actions a floated panel's window offers, before its close button. */
export function floatActions(panel: RegisteredPanel): PanelAction[] {
  return [
    {id: "dock", label: "Dock back into the panel column", icon: DockGlyph, run: () => update((d) => dockPanel(d, panel.id))},
    {id: "minimise", label: "Minimise", icon: MinimiseGlyph, run: () => update((d) => minimisePanel(d, panel.id))},
  ];
}

/** Does the column leave this panel to the float layer. */
export function isUndocked(doc: LayoutDoc, id: PanelId): boolean {
  return !!doc.floats[id];
}

function UndockedWindow({panel, doc, contents}: {panel: RegisteredPanel; doc: LayoutDoc; contents?: Readonly<Record<PanelId, React.ComponentType<PanelProps>>>}) {
  const f = doc.floats[panel.id]!;
  const close = () => void hide(panel.id);
  const props: PanelProps = {panelId: panel.id, host: {kind: "float", zone: "classic.float"}, close, isActive: true};
  return (
    <FloatingWindow
      dragHandleClassName={DRAG_HANDLE}
      zIndex={45}
      zClassName="z-[45]"
      overlay
      fullScreenWhenNarrow
      minSize={MIN_SIZE}
      style={{zIndex: f.z}}
      rect={{x: f.x, y: f.y, width: f.w, height: f.h}}
      onRectChange={(r) => update((d) => setFloatRect(d, panel.id, {x: r.x, y: r.y, w: r.width, h: r.height}))}
    >
      {({fullScreen}) => (
        <div className="h-full w-full" onPointerDownCapture={() => update((d) => raiseFloat(d, panel.id))}>
          <PanelFrame
            panelId={panel.id}
            title={panel.title}
            host={props.host}
            actions={floatActions(panel)}
            onClose={panel.capabilities.closable ? close : undefined}
            dragHandleClassName={fullScreen ? undefined : DRAG_HANDLE}
          >
            {/* The column card gave the content its text colour and padding; the window body does not. */}
            <div className="h-full overflow-y-auto p-2 text-sm text-[var(--ada-panel-text)]">
              <PanelMount panel={panel} provided={contents?.[panel.id]} props={props} which="content" outerSuspense />
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
          onClick={() => update((d) => restorePanel(d, p.id))}
          title={`Restore ${p.title}`}
          aria-label={`Restore ${p.title}`}
        >
          {p.icon ? <p.icon /> : <span className="text-xs px-1">{p.title}</span>}
        </button>
      ))}
    </div>
  );
}
