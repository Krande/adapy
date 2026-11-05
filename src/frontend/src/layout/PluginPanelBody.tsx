// The registry body of a plugin's panel slot (see `./pluginPanels`): the slot's
// `render(ctx)` in its plugin's context, inside the same error boundary the
// classic region host uses, so a throwing panel disables its plugin wherever a
// host mounts it. Loaded lazily; nothing mounts it in phase 0.

import React from "react";

import { PluginPanelMount } from "@/plugins/PluginSlots";
import { makePluginContext } from "@/plugins/context";
import type { PanelSlot } from "@/plugins/registry";
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
