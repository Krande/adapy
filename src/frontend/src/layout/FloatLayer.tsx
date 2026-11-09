// A float zone: the open panels of one floating-window zone of the active preset.
//
// One host for every window a preset floats. A panel that draws its own window
// (`frame: "own"`, e.g. the attribute search) is mounted as it is. A panel
// whose frame is the host's (`frame: "host"`) is put in a window: the preset's
// window chrome for that panel when it has one (the classic in-viewer window
// keeps its title bar and "open in a dedicated tab"), else a `FloatingWindow`
// with the shared `PanelFrame`.
//
// Which panels are mounted is the preset's call (`mount`), from each panel's
// open / offered state; the default is "open".

import React from "react";

import {FloatingWindow} from "@/components/ui/FloatingWindow";
import {TransformedHost} from "@/components/ui/OverlayPortal";
import {PanelFrame, WindowDockProvider, type WindowDock} from "@/components/ui/PanelFrame";

import {PanelMount, usePanelStates, useLayout, zonePanels} from "./LayoutHost";
import type {PanelEnv, PanelId, PanelProps, RegisteredPanel, ZoneId} from "./panel";
import type {PanelState} from "./panelStates";
import {hide} from "./reveal";

/** The window around a host-framed panel. */
export type WindowChrome = React.ComponentType<{panel: RegisteredPanel; close: () => void; children: React.ReactNode}>;

export type MountRule = (state: PanelState, env: PanelEnv) => boolean;

export const mountWhenOpen: MountRule = (s) => s.open;

const DRAG_HANDLE = "layout-float-drag-handle";

/** The window a host-framed panel gets when its preset has no chrome of its own for it. */
export const DefaultWindowChrome: WindowChrome = ({panel, close, children}) => {
  const size = panel.placement?.default?.float ?? {w: 640, h: 480};
  return (
    <FloatingWindow
      dragHandleClassName={DRAG_HANDLE}
      zIndex={60}
      zClassName="z-[60]"
      minSize={panel.constraints?.minSize?.w && panel.constraints.minSize.h ? {width: panel.constraints.minSize.w, height: panel.constraints.minSize.h} : undefined}
      initialRect={() => ({
        x: Math.max(0, (window.innerWidth - size.w) / 2),
        y: 60,
        width: Math.min(size.w, window.innerWidth - 32),
        height: Math.min(size.h, window.innerHeight - 80),
      })}
      fullScreenWhenNarrow
    >
      {({fullScreen}) => (
        <PanelFrame
          panelId={panel.id}
          title={panel.title}
          host={{kind: "float", zone: panel.placement?.default?.zone ?? "float"}}
          actions={[]}
          onClose={panel.capabilities.closable ? close : undefined}
          dragHandleClassName={fullScreen ? undefined : DRAG_HANDLE}
        >
          {/* The window moves by a transform: fixed overlays of the content portal out (`OverlayPortal`). */}
          <TransformedHost>{children}</TransformedHost>
        </PanelFrame>
      )}
    </FloatingWindow>
  );
};

export interface FloatLayerProps {
  zone: ZoneId;
  /** When a panel is mounted. Default: while open. */
  mount?: (panel: RegisteredPanel, state: PanelState, env: PanelEnv) => boolean;
  /** The preset's window for a host-framed panel, if it has one. */
  chromeFor?: (panel: RegisteredPanel) => WindowChrome | undefined;
  /** Components the preset provides synchronously, by panel id. */
  provided?: Readonly<Record<PanelId, React.ComponentType<PanelProps>>>;
  /** A panel the preset shows elsewhere right now (e.g. docked): not mounted here. */
  skip?: (panel: RegisteredPanel) => boolean;
  /** What the preset lets a panel's window do (`WindowDockProvider`); null: nothing more. */
  windowDock?: (panel: RegisteredPanel) => WindowDock | null;
}

export function FloatLayer({zone, mount, chromeFor, provided, skip, windowDock}: FloatLayerProps) {
  const {preset, env} = useLayout();
  const panels = zonePanels(preset, zone);
  const states = usePanelStates(panels, env);
  return (
    <>
      {panels.map((panel) => {
        const state = states.get(panel.id)!;
        if (!(mount ? mount(panel, state, env) : mountWhenOpen(state, env))) return null;
        if (skip?.(panel)) return null;
        const close = () => void hide(panel.id);
        const props: PanelProps = {panelId: panel.id, host: {kind: "float", zone}, close, isActive: true};
        let win: React.ReactNode;
        if (panel.frame === "own") {
          win = <PanelMount panel={panel} provided={provided?.[panel.id]} props={props} />;
        } else {
          const Chrome = chromeFor?.(panel) ?? DefaultWindowChrome;
          // The chrome's frame has the Suspense boundary (and its "Loading…" line) a lazy body needs.
          win = (
            <Chrome panel={panel} close={close}>
              <PanelMount panel={panel} provided={provided?.[panel.id]} props={props} outerSuspense />
            </Chrome>
          );
        }
        return (
          <WindowDockProvider key={panel.id} value={windowDock?.(panel) ?? null}>
            {win}
          </WindowDockProvider>
        );
      })}
    </>
  );
}
