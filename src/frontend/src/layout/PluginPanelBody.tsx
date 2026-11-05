// The registry body of a plugin's panel slot (see `./pluginPanels`): the slot's
// `render(ctx)` in its plugin's context, inside the same error boundary the
// classic region host uses, so a throwing panel disables its plugin wherever a
// host mounts it. Loaded lazily; nothing mounts it in phase 0.

import React from "react";

import { PluginPanelMount } from "@/plugins/PluginSlots";
import { makePluginContext } from "@/plugins/context";
import { disablePlugin, type PanelSlot } from "@/plugins/registry";
import { useViewerStores } from "@/state/AdaViewerContext";
import { usePluginTheme } from "@/state/themeStore";
import type { PanelProps } from "./panel";

export function makePluginPanelBody(
  pluginId: string,
  slot: PanelSlot,
): React.ComponentType<PanelProps> {
  const Body: React.FC<PanelProps> = () => {
    const stores = useViewerStores();
    const theme = usePluginTheme();
    return <PluginPanelMount ctx={makePluginContext(pluginId, stores, theme)} panel={slot} />;
  };
  Body.displayName = `PluginPanelBody(${slot.id})`;
  return Body;
}

/**
 * The same panel as content of a place the layout gives it (a group of the
 * grid, a window). Its region host shows a buttonless panel only while its
 * activation predicate holds; here the place stays, so it says there is nothing
 * to show instead.
 */
export function makePluginPanelContent(
  pluginId: string,
  slot: PanelSlot,
): React.ComponentType<PanelProps> {
  const Content: React.FC<PanelProps> = () => {
    const stores = useViewerStores();
    const theme = usePluginTheme();
    const ctx = makePluginContext(pluginId, stores, theme);
    let active = true;
    try {
      active = slot.activationPredicate ? slot.activationPredicate(ctx) : true;
    } catch (err) {
      disablePlugin(pluginId, `panel "${slot.id}" activationPredicate threw: ${String(err)}`);
      active = false;
    }
    if (!active) return <div className="p-2 text-xs italic text-gray-400">Nothing to show here yet.</div>;
    return <PluginPanelMount ctx={ctx} panel={slot} />;
  };
  Content.displayName = `PluginPanelContent(${slot.id})`;
  return Content;
}
