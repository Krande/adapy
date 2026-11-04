// Adapts a plugin's `PanelSlot` to a `PanelSpec`, so plugin panels sit in the
// same registry as core's and a host lists them the same way.
//
// `registerPlugin` calls this for every slot it accepts. Nothing about a slot
// changes: core's classic UI still mounts plugin panels by region through
// `PluginPanelRegion` / `PluginTopBarButtons`, exactly as before. The spec is
// the same panel described in the registry's terms, with the slot's region and
// dock translated into placements.
//
// Dependency-free: the panel body (which needs React, the plugin context and an
// error boundary) is behind a dynamic import, and the store that holds plugin
// panel visibility is handed in by the bootstrap (`setPluginPanelVisibility`)
// rather than imported, because importing it would pull zustand into
// `@/plugins/registry`.

import type { PanelSlot, PluginDockId } from "@/plugins/registry";
import { CLASSIC_PRESET_ID, CLASSIC_ZONES, DOCKED_PRESET_ID } from "./layout";
import type { PanelBinding, PanelPlacement, PanelPlacements, PanelSpec } from "./panel";

/**
 * Plugin top-bar toggles sit after core's own (`core:cellbuilder`) and before the
 * connection status, as `Menu.tsx` renders them. All share this slot; their
 * `subOrder` is the slot's own `order`.
 */
export const PLUGIN_TOOLBAR_ORDER = 500;

/** Plugin `top-panel` panels stack after every core panel of the menu column. */
export const PLUGIN_MENU_COLUMN_ORDER = 1000;

/** The docks a plugin may name. Anything else falls back to `right`. */
const KNOWN_DOCKS: ReadonlySet<string> = new Set<PluginDockId>([
  "left",
  "right",
  "right-aux",
  "right-aux2",
  "bottom",
  "float",
  "overlay",
]);

/** A plugin slot's `dock`, or `right` for an id no preset knows. */
export function dockZoneFor(dock: string): string {
  return KNOWN_DOCKS.has(dock) ? dock : "right";
}

/** The visibility store of plugin panels with a top-bar button (`pluginUiStore`), structurally. */
export interface PluginPanelVisibility {
  getState(): {
    visible: Record<string, boolean>;
    setVisible(panelId: string, v: boolean): void;
  };
  subscribe(
    listener: (
      state: { visible: Record<string, boolean> },
      prev: { visible: Record<string, boolean> },
    ) => void,
  ): () => void;
}

let _visibility: PluginPanelVisibility | null = null;

/** Hand the adapter the plugin panel visibility store. Called once by the bootstrap. */
export function setPluginPanelVisibility(store: PluginPanelVisibility | null): void {
  _visibility = store;
}

/**
 * The binding of a top-bar plugin panel: its key in the visibility store, which
 * is what `PluginTopBarButtons` toggles and `PluginPanelRegion` reads. Resolved
 * at call time, so a slot registered before the bootstrap handed the store over
 * still binds to it.
 */
function pluginVisibilityBinding(panelId: string): PanelBinding {
  return {
    isOpen: () => !!_visibility?.getState().visible[panelId],
    setOpen: (open) => _visibility?.getState().setVisible(panelId, open),
    subscribe: (cb) =>
      _visibility
        ? _visibility.subscribe((s, prev) => {
            if (!!s.visible[panelId] !== !!prev.visible[panelId]) cb();
          })
        : () => {},
  };
}

/** Where the classic UI mounts a slot, by region. */
function classicPlacement(slot: PanelSlot): PanelPlacement {
  switch (slot.region) {
    case "top-panel":
      return { zone: CLASSIC_ZONES.menuColumn, order: PLUGIN_MENU_COLUMN_ORDER + (slot.order ?? 0) };
    case "fem-sidebar":
      return {
        zone: CLASSIC_ZONES.menuColumn,
        parent: "core:simulation",
        as: slot.asTab ? "tab" : "inline",
        order: slot.asTab ? (slot.asTab.order ?? 0) : (slot.order ?? 0),
      };
    case "admin":
      return { zone: CLASSIC_ZONES.window, parent: "core:admin", as: "tab", order: slot.order ?? 0 };
    case "scene-info":
      return { zone: CLASSIC_ZONES.menuColumn, parent: "core:scene", as: "section", order: slot.order ?? 0 };
    case "storage-detail":
      return { zone: CLASSIC_ZONES.menuColumn, parent: "core:storage", as: "section", order: slot.order ?? 0 };
    default:
      // An unknown region: a newer plugin on an older core. Classic mounts it
      // nowhere (no region host matches), so the placement only records intent.
      return { zone: CLASSIC_ZONES.menuColumn, order: PLUGIN_MENU_COLUMN_ORDER + (slot.order ?? 0) };
  }
}

/**
 * The `PanelSpec` for one of a plugin's slots. `slot.id` is already namespaced
 * (`<pluginId>:<local>`). `isDisabled` reports whether the plugin has been
 * disabled for the session; a disabled plugin's panels are unavailable.
 */
export function panelSpecFromSlot(
  pluginId: string,
  slot: PanelSlot,
  isDisabled: () => boolean,
): PanelSpec {
  const local = slot.id.startsWith(`${pluginId}:`) ? slot.id.slice(pluginId.length + 1) : slot.id;
  const placement: PanelPlacements = { [CLASSIC_PRESET_ID]: classicPlacement(slot) };
  if (slot.dock) placement[DOCKED_PRESET_ID] = { zone: dockZoneFor(slot.dock), order: slot.order ?? 0 };

  const isToolbar = slot.region === "top-panel" && !!slot.topBarButton;
  return {
    id: slot.id,
    title: slot.asTab?.label ?? slot.topBarButton?.label ?? local,
    icon: slot.topBarButton?.icon,
    hint: slot.topBarButton?.label,
    body: () =>
      import("./PluginPanelBody").then((m) => ({ default: m.makePluginPanelBody(pluginId, slot) })),
    // A slot with a top-bar button is opened and closed by that button. One
    // without is shown whenever its activation predicate holds, so it has no
    // visibility of its own to bind.
    binding: slot.topBarButton ? pluginVisibilityBinding(slot.id) : undefined,
    // Activation predicates need a plugin context, which only a mounted host
    // has; the region hosts keep evaluating them. Here: not disabled.
    available: () => !isDisabled(),
    placement,
    toolbar: isToolbar ? { order: PLUGIN_TOOLBAR_ORDER, subOrder: slot.order ?? 0 } : undefined,
    modes: slot.modes,
    frame: "own",
  };
}
