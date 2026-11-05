// The layout host: renders a layout preset from the panel registry.
//
// It owns three things and leaves the rest to the preset's frame:
//
//   * the PANEL CONTROLLER -- while mounted, `reveal(id)` / `hide(id)` mean what
//     the preset says (`./controller`); unmounted, the binding controller again;
//   * the LAYOUT CONTEXT -- the preset and the env (`PanelEnv`) every zone reads;
//   * the PANEL HOOKS a frame renders zones with: the panels of a zone, their
//     open / offered state (re-rendering exactly when one changes), and a panel's
//     component (synchronous when the preset provides it, else its lazy body).
//
// The frame itself -- which zones exist, where they sit around the viewport, the
// markup between them -- is the preset's (`LayoutPreset.frame`). The viewport is
// rendered once, by the frame, and never inside a movable container.

import React, {Suspense, createContext, useContext, useLayoutEffect, useMemo, useSyncExternalStore} from "react";
import type {StoreApi} from "zustand/vanilla";

import {createLayoutController} from "./controller";
import type {LayoutPreset} from "./layout";
import {layoutStore, type LayoutStoreState} from "./layoutStore";
import {placementFor, type PanelEnv, type PanelId, type PanelProps, type RegisteredPanel, type ZoneId} from "./panel";
import {panelStates, panelStatesKey, subscribePanels, type PanelState} from "./panelStates";
import {listPanels} from "./registry";
import {isPanelOpen, setPanelController} from "./reveal";

export interface LayoutContextValue {
  preset: LayoutPreset;
  env: PanelEnv;
}

const LayoutContext = createContext<LayoutContextValue | null>(null);

/** The active preset and env. Throws outside a `LayoutHost`. */
export function useLayout(): LayoutContextValue {
  const ctx = useContext(LayoutContext);
  if (!ctx) throw new Error("useLayout() outside a <LayoutHost>");
  return ctx;
}

/**
 * The panels placed in `zone` by the active preset, in zone order (then
 * registration order). Panels hosted INSIDE another panel (`placement.parent`)
 * are the parent's to mount, not the zone's.
 */
export function zonePanels(preset: LayoutPreset, zone: ZoneId, panels: readonly RegisteredPanel[] = listPanels()): RegisteredPanel[] {
  return panels
    .map((p, i) => ({p, i, pl: placementFor(p, preset.id)}))
    .filter(({pl}) => pl && pl.zone === zone && !pl.parent)
    .sort((a, b) => (a.pl!.order ?? 0) - (b.pl!.order ?? 0) || a.i - b.i)
    .map(({p}) => p);
}

/**
 * Each panel's open / offered state, re-rendering the caller exactly when one
 * changes (a binding's flag, or a store a spec's `available` watches).
 */
export function usePanelStates(panels: readonly RegisteredPanel[], env: PanelEnv): ReadonlyMap<PanelId, PanelState> {
  const ids = panels.map((p) => p.id).join(",");
  const subscribe = useMemo(
    () => (cb: () => void) => subscribePanels(panels, cb),
    // The panel list is identified by its ids: a re-render must not resubscribe.
    [ids],
  );
  const controller = useMemo(() => ({isOpen: isPanelOpen}), []);
  const key = useSyncExternalStore(subscribe, () => panelStatesKey(panelStates(panels, env, controller)));
  return useMemo(() => panelStates(panels, env, controller), [key, env]);
}

const lazyBodies = new Map<string, React.LazyExoticComponent<React.ComponentType<PanelProps>>>();

/** A panel's lazily loaded body (or `content`), one lazy component per panel for the page's life. */
export function lazyPanelBody(panel: RegisteredPanel, which: "body" | "content" = "body"): React.LazyExoticComponent<React.ComponentType<PanelProps>> {
  const key = `${which}:${panel.id}`;
  let lazy = lazyBodies.get(key);
  if (!lazy) {
    const load = which === "content" && panel.content ? panel.content : panel.body;
    lazy = React.lazy(load);
    lazyBodies.set(key, lazy);
  }
  return lazy;
}

/**
 * A panel's component: the one the preset provides synchronously (no Suspense,
 * so first paint matches a statically imported panel), else its lazy body inside
 * a Suspense boundary with `fallback` -- or none with `outerSuspense`, when the
 * frame around it already has the boundary. `which: "content"` loads the
 * panel's frameless content, for a host that draws the frame.
 */
export function PanelMount({
  panel,
  provided,
  props,
  fallback = null,
  outerSuspense = false,
  which = "body",
}: {
  panel: RegisteredPanel;
  provided?: React.ComponentType<PanelProps>;
  props: PanelProps;
  fallback?: React.ReactNode;
  outerSuspense?: boolean;
  which?: "body" | "content";
}) {
  // A shell's options for the panel's content reach it as `PanelProps.options`.
  if (panel.options && !props.options) props = {...props, options: panel.options};
  if (provided) {
    const C = provided;
    return <C {...props} />;
  }
  const Lazy = lazyPanelBody(panel, which);
  if (outerSuspense) return <Lazy {...props} />;
  return (
    <Suspense fallback={fallback}>
      <Lazy {...props} />
    </Suspense>
  );
}

export interface LayoutHostProps {
  preset: LayoutPreset;
  env: PanelEnv;
  /** Where layout-owned state lives. The viewer's store by default. */
  store?: StoreApi<LayoutStoreState>;
  /** The document context within the preset (`LayoutDoc.context`). Default: `"default"`. */
  context?: string;
}

export function LayoutHost({preset, env, store = layoutStore, context}: LayoutHostProps) {
  // Before children's effects, so a panel that reveals another on mount already
  // goes through this preset's controller.
  useLayoutEffect(() => {
    const groups = preset.exclusiveGroups;
    setPanelController(
      createLayoutController({
        preset: preset.id,
        zones: preset.zones,
        store,
        context,
        exclusive: groups ? () => groups(store.getState().prefs) : undefined,
      }),
    );
    return () => setPanelController(null);
  }, [preset, store, context]);

  const Frame = useMemo(() => (preset.frame ? React.lazy(preset.frame) : null), [preset]);
  const value = useMemo(() => ({preset, env}), [preset, env]);
  if (!Frame) return null;
  return (
    <LayoutContext.Provider value={value}>
      <Suspense fallback={null}>
        <Frame />
      </Suspense>
    </LayoutContext.Provider>
  );
}
