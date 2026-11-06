// The HUD layer over the 3D view: a stack at each anchor (`./viewportOverlays`),
// plus, for a composed shell that places them, core's colour legend and the
// plugin panels that name the `overlay` dock. Only the HUDs take the pointer.

import React, {Suspense, useSyncExternalStore} from "react";

import ErrorBoundary from "@/components/common/ErrorBoundary";
import ColorLegend from "@/components/viewer/ColorLegend";

import {ColorByLegend} from "./ColorByLegend";
import {NumberLabels} from "./NumberLabels";
import {PanelMount} from "./LayoutHost";
import type {RegisteredPanel} from "./panel";
import {
  VIEWPORT_ANCHORS,
  overlaysAt,
  subscribeViewportOverlays,
  viewportOverlaysVersion,
  type ViewportAnchor,
} from "./viewportOverlays";

const ANCHOR_CLASS: Readonly<Record<ViewportAnchor, string>> = {
  "top-left": "left-2 top-2 items-start",
  "top-center": "left-1/2 top-2 -translate-x-1/2 items-center",
  "top-right": "right-2 top-2 items-end",
  "left-center": "left-2 top-1/2 -translate-y-1/2 items-start",
  "right-center": "right-2 top-1/2 -translate-y-1/2 items-end",
  "bottom-left": "bottom-2 left-2 flex-col-reverse items-start",
  "bottom-center": "bottom-2 left-1/2 -translate-x-1/2 flex-col-reverse items-center",
  "bottom-right": "bottom-2 right-2 flex-col-reverse items-end",
};

export interface ViewportOverlayPlacement {
  /** Where core's colour legend goes (the canvas then draws none of its own). */
  legend?: ViewportAnchor;
  /** Where plugin panels that name the `overlay` dock stack. */
  pluginPanels?: ViewportAnchor;
  /** Where the legend of a colour-by core's colour legend does not draw (the models, a registered source) goes. */
  colorByLegend?: ViewportAnchor;
  /** Draw core's number labels (node and element ids of a loaded FEA result) over the view. */
  labels?: boolean;
}

export function ViewportOverlays({placement, pluginPanels}: {placement: ViewportOverlayPlacement; pluginPanels: readonly RegisteredPanel[]}) {
  useSyncExternalStore(subscribeViewportOverlays, viewportOverlaysVersion, viewportOverlaysVersion);
  return (
    <div className="pointer-events-none absolute inset-0 z-[4]" data-viewport-overlays="">
      {placement.labels && <NumberLabels />}
      {VIEWPORT_ANCHORS.map((anchor) => {
        const items: React.ReactNode[] = [];
        if (placement.legend === anchor) items.push(<ColorLegend key="core:legend" />);
        if (placement.colorByLegend === anchor) items.push(<ColorByLegend key="core:color-by-legend" />);
        if (placement.pluginPanels === anchor) {
          for (const p of pluginPanels) {
            items.push(
              <ErrorBoundary key={p.id} label={p.title}>
                <Suspense fallback={null}>
                  <PanelMount panel={p} props={{panelId: p.id, host: {kind: "overlay", zone: "viewport"}, close: () => {}, isActive: true}} which="content" outerSuspense />
                </Suspense>
              </ErrorBoundary>,
            );
          }
        }
        for (const o of overlaysAt(anchor)) {
          let shown = true;
          try {
            shown = !o.when || o.when();
          } catch {
            shown = false;
          }
          if (shown) items.push(<ErrorBoundary key={o.id} label={o.id}>{o.render()}</ErrorBoundary>);
        }
        if (!items.length) return null;
        return (
          <div key={anchor} className={`absolute flex max-h-full flex-col gap-2 ${ANCHOR_CLASS[anchor]}`} data-viewport-anchor={anchor}>
            {items.map((node, i) => (
              <div key={i} className="pointer-events-auto">
                {node}
              </div>
            ))}
          </div>
        );
      })}
    </div>
  );
}
