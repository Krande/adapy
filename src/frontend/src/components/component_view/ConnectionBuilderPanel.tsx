// The connection builder as a panel of its own (`core:component-controls`, the top bar's
// connection button): the same builder as the Scene panel's Connection tab -- pick a spec, edit
// each member, see the joint rebuilt -- in the menu column on desktop and a bottom sheet on phones.
//
// It replaced the "Connection components" panel, which offered only the specs a CI bake had
// published to a scope. The builder's specs are the ones the live workers' code registers (or, on
// `ada dev connections`, the served library's), offered per scope as the admin assigned them.
//
// A plugin's tab in the connection builder (region `connection-builder`) is shown here, after the
// builder's own tab; with no such tab the panel has no tab strip. The Scene panel's Connection tab
// stays the builder alone.
//
// The panel is named "Connections" (it was "Connection builder"): it is where everything about
// connections is reached. Where the builder itself is not offered -- the scope offers no specs --
// the panel is still there for the plugins' tabs, and the builder's own tab is left out.

import React, { useEffect } from "react";

import { SimTabButton } from "@/components/simulation/SimulationControls";
import { SimulationsFrame } from "@/components/simulations/SimulationsFrame";
import { PanelHeaderActions, usePanelHeaderActions } from "@/components/ui/PanelFrame";
import { usePlacedElsewhereVersion } from "@/layout/usePlacedElsewhere";
import { makePluginContext } from "@/plugins/context";
import { PluginPanelMount } from "@/plugins/PluginSlots";
import {
  disablePlugin,
  getConnectionBuilderTabs,
  pluginActivationVersion,
  subscribePluginActivation,
  type AdaPluginContext,
  type SimulationTabEntry,
} from "@/plugins/registry";
import {
  markConnectionBuilderSceneReady,
  startConnectionBuilderSession,
} from "@/services/devConnections/connectionBuilderSession";
import { useViewerStores } from "@/state/AdaViewerContext";
import { useComponentControlsStore } from "@/state/componentControlsStore";
import { useConnectionsOfferStore } from "@/state/connectionsOfferStore";
import { runtime } from "@/runtime/config";
import { BUILDER_TAB, useConnectionBuilderTabStore } from "@/state/connectionBuilderTabStore";
import { usePluginTheme } from "@/state/themeStore";

import { ConnectionBuilderBody } from "./ConnectionBuilder";

export const CONNECTION_BUILDER_TITLE = "Connections";

/** The builder's own tab, as the strip names it. */
export const BUILDER_TAB_LABEL = "Builder";

/** The builder itself; showing it starts the builder's loop (once per page). */
const BuilderTab: React.FC = () => {
  useEffect(() => {
    startConnectionBuilderSession();
    // The panel opens on a mounted viewer: the scene is there for the first build to load into.
    markConnectionBuilderSceneReady();
  }, []);
  return <ConnectionBuilderBody />;
};

const BuilderTabStrip: React.FC<{
  tabs: SimulationTabEntry[];
  activeTab: string;
  onSelect: (id: string) => void;
  ctxFor: (pluginId: string) => AdaPluginContext;
  /** Whether the builder's own tab is there: not where the builder is not offered. */
  builder: boolean;
}> = ({ tabs, activeTab, onSelect, ctxFor, builder }) => (
  <div
    className="flex flex-wrap gap-0.5 border-b border-white/15"
    role="tablist"
    aria-label="Connections section"
  >
    {builder && (
      <SimTabButton
        id={BUILDER_TAB}
        label={BUILDER_TAB_LABEL}
        active={activeTab === BUILDER_TAB}
        onClick={() => onSelect(BUILDER_TAB)}
      />
    )}
    {tabs.map((t) => {
      let badge: number | string | null = null;
      try {
        badge = t.asTab.badge ? t.asTab.badge(ctxFor(t.pluginId)) : null;
      } catch (err) {
        disablePlugin(t.pluginId, `tab badge threw: ${String(err)}`);
        badge = null;
      }
      return (
        <SimTabButton
          key={t.panel.id}
          id={t.panel.id}
          label={t.asTab.label}
          active={activeTab === t.panel.id}
          contextual={t.asTab.contextual}
          badge={badge}
          onClick={() => onSelect(t.panel.id)}
        />
      );
    })}
  </div>
);

/** The panel's content: the builder, and after it the plugins' tabs when there are any. */
export const ConnectionBuilderPanelContent: React.FC = () => {
  const stores = useViewerStores();
  const theme = usePluginTheme();
  // A tab the layout shows in a group or window of its own leaves the strip...
  usePlacedElsewhereVersion();
  // ...and one whose plugin says its activation changed is asked again.
  React.useSyncExternalStore(subscribePluginActivation, pluginActivationVersion, pluginActivationVersion);
  const picked = useConnectionBuilderTabStore((s) => s.tab);
  const select = useConnectionBuilderTabStore((s) => s.select);

  // The builder's own tab is there where the builder is offered (`connectionBuilderOffered`).
  const offered = useConnectionsOfferStore((s) => s.offered);
  const builder = runtime.devConnections() || offered;

  const tabs = getConnectionBuilderTabs(makePluginContext("", stores, theme));
  // No plugin tab: the builder alone, with no strip.
  if (tabs.length === 0) {
    return builder ? <BuilderTab /> : <div className="text-xs text-gray-400">Nothing about connections is offered in this scope.</div>;
  }

  // A tab that went away (its plugin deactivated or disabled) falls back to the builder, or
  // where there is none to the first tab there is.
  const active = tabs.find((t) => t.panel.id === picked) ?? (builder ? undefined : tabs[0]);
  return (
    <div className="flex flex-col gap-2 min-w-0">
      <BuilderTabStrip
        tabs={tabs}
        builder={builder}
        activeTab={active ? active.panel.id : BUILDER_TAB}
        onSelect={select}
        ctxFor={(pid) => makePluginContext(pid, stores, theme)}
      />
      {/* Only the tab on show is mounted: one left is unmounted, and ends what it was doing. */}
      {active ? (
        <PluginPanelMount
          key={active.panel.id}
          ctx={makePluginContext(active.pluginId, stores, theme)}
          panel={active.panel}
        />
      ) : (
        <BuilderTab />
      )}
    </div>
  );
};

/** The panel in its classic frame: shown while its flag is set. */
const ConnectionBuilderPanel: React.FC = () => {
  const isVisible = useComponentControlsStore((s) => s.isVisible);
  const setIsVisible = useComponentControlsStore((s) => s.setIsVisible);
  const hosted = usePanelHeaderActions().length > 0;
  if (!isVisible) return null;
  return (
    <SimulationsFrame
      onDismiss={() => setIsVisible(false)}
      header={
        <>
          <span className="font-bold">{CONNECTION_BUILDER_TITLE}</span>
          {hosted && <PanelHeaderActions />}
        </>
      }
    >
      <ConnectionBuilderPanelContent />
    </SimulationsFrame>
  );
};

export default ConnectionBuilderPanel;
