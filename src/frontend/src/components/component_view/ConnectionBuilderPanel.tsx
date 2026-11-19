// The Connections panel (`core:component-controls`, the top bar's connection button): where
// everything about connections is reached, in the menu column on desktop and a bottom sheet on
// phones. Its tabs (`./connectionsTabs`):
//
//   * Builder -- the connection builder: pick a spec, edit each member, see the joint rebuilt.
//     Left out where the builder is not offered (the scope offers no specs).
//   * Clashes, Overlays and Review -- the joint workflow that was the Scene panel's Joints group:
//     identify joints in the loaded source, what the joint tabs draw in 3D, and the review of the
//     joints found or detailed (only while there are any, or while a plugin has a section of
//     Review to show: region `joint-review`, rendered after core's own sections).
//   * then a plugin's tabs in the connection builder (region `connection-builder`), after core's.
//
// Clashes and Overlays are always there, so the panel always has a tab strip and something to
// show. (With the builder and no plugin tab it used to be the builder alone, with no strip; with
// neither it said "Nothing about connections is offered in this scope.")
//
// It replaced the "Connection components" panel, which offered only the specs a CI bake had
// published to a scope. The builder's specs are the ones the live workers' code registers (or, on
// `ada dev connections`, the served library's), offered per scope as the admin assigned them. The
// panel was named "Connection builder" before it was "Connections".
//
// It stays in the main window: Clashes, Overlays and the builder act on the scene, and the panel
// is neither mirrored nor offered for a pop-out (`layout/popout`'s `canPopOut`).

import React, { useEffect } from "react";

import { SimTabButton } from "@/components/simulation/SimulationControls";
import { SimulationsFrame } from "@/components/simulations/SimulationsFrame";
import { PanelHeaderActions, usePanelHeaderActions } from "@/components/ui/PanelFrame";
import ClashesPanel from "@/components/info_box_scene/ClashesPanel";
import JointOverlaysPanel from "@/components/info_box_scene/JointOverlaysPanel";
import JointsOverviewPanel from "@/components/info_box_scene/JointsOverviewPanel";
import { usePlacedElsewhereVersion } from "@/layout/usePlacedElsewhere";
import { makePluginContext } from "@/plugins/context";
import { PluginPanelMount } from "@/plugins/PluginSlots";
import {
  disablePlugin,
  getConnectionBuilderTabs,
  getJointReviewSections,
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
import { useClashCheckStore } from "@/state/clashCheckStore";
import { useComponentControlsStore } from "@/state/componentControlsStore";
import { useConnectionsOfferStore } from "@/state/connectionsOfferStore";
import { useJointFocusStore } from "@/state/jointFocusStore";
import { useStatsStore } from "@/state/statsStore";
import { runtime } from "@/runtime/config";
import {
  BUILDER_TAB,
  CLASHES_TAB,
  JOINT_OVERLAYS_TAB,
  useConnectionBuilderTabStore,
} from "@/state/connectionBuilderTabStore";
import { usePluginTheme } from "@/state/themeStore";

import { ConnectionBuilderBody } from "./ConnectionBuilder";
import { CONNECTIONS_CORE_TABS, connectionsActiveTab, connectionsCoreTabs, type ConnectionsTabDef } from "./connectionsTabs";

export const CONNECTION_BUILDER_TITLE = "Connections";

/** The builder's own tab, as the strip names it. */
export const BUILDER_TAB_LABEL = CONNECTIONS_CORE_TABS[0].label;

/** The builder itself; showing it starts the builder's loop (once per page). */
const BuilderTab: React.FC = () => {
  useEffect(() => {
    startConnectionBuilderSession();
    // The panel opens on a mounted viewer: the scene is there for the first build to load into.
    markConnectionBuilderSceneReady();
  }, []);
  return <ConnectionBuilderBody />;
};

/**
 * Whether there are joints to review. It used to be only when the loaded model's take-off carried
 * fabrication-detail joints (a model compiled with a detailing engine) -- "PRODUCED" joints. It is
 * ALSO when a Clashes run has IDENTIFIED joints in the loaded source, before anything has been
 * detailed: identification and review are two ends of one workflow, so the tab that reviews joints
 * must exist as soon as either half has something to show.
 */
function useHasJoints(): boolean {
  const produced = useStatsStore((s) => (s.stats?.joints?.count ?? 0) > 0);
  // Joints generated in THIS session count as produced too: they arrive as an overlay, which has
  // no stats of its own, so the compiled model's take-off never sees them.
  const generated = useClashCheckStore((s) => (s.producedJoints?.count ?? 0) > 0);
  const identified = useClashCheckStore((s) => (s.result?.joints.length ?? 0) > 0);
  // A joint of a model on screen opened from a picked weld ("Show joint") is reviewed there too.
  const shown = useJointFocusStore((s) => s.joints.length > 0);
  return produced || generated || identified || shown;
}

/** One of core's tabs' content. */
const CoreTabContent: React.FC<{ tab: ConnectionsTabDef["id"] }> = ({ tab }) =>
  tab === BUILDER_TAB ? (
    <BuilderTab />
  ) : tab === CLASHES_TAB ? (
    <ClashesPanel />
  ) : tab === JOINT_OVERLAYS_TAB ? (
    <JointOverlaysPanel />
  ) : (
    <JointsOverviewPanel />
  );

const ConnectionsTabStrip: React.FC<{
  core: ConnectionsTabDef[];
  tabs: SimulationTabEntry[];
  activeTab: string | undefined;
  onSelect: (id: string) => void;
  ctxFor: (pluginId: string) => AdaPluginContext;
}> = ({ core, tabs, activeTab, onSelect, ctxFor }) => (
  <div
    className="flex flex-wrap gap-0.5 border-b border-white/15"
    role="tablist"
    aria-label="Connections section"
  >
    {core.map((t) => (
      <SimTabButton
        key={t.id}
        id={t.id}
        label={t.label}
        active={activeTab === t.id}
        contextual={t.ctx}
        onClick={() => onSelect(t.id)}
      />
    ))}
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

/** The panel's content: core's tabs, then the plugins' tabs, and the one on show. */
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
  // Review is there while there are joints, or while a plugin has a section of it to show.
  const reviewSections = getJointReviewSections(makePluginContext("", stores, theme));
  const joints = useHasJoints() || reviewSections.length > 0;
  const core = connectionsCoreTabs({ builder, joints });

  const tabs = getConnectionBuilderTabs(makePluginContext("", stores, theme));
  // A tab that went away (Review with no joints left, a plugin deactivated or disabled) falls
  // back to the first tab there is: the builder where it is offered, else Clashes.
  const activeId = connectionsActiveTab(picked, [...core.map((t) => t.id), ...tabs.map((t) => t.panel.id)]);
  const activeCore = core.find((t) => t.id === activeId);
  const activePlugin = tabs.find((t) => t.panel.id === activeId);
  return (
    <div className="flex flex-col gap-2 min-w-0">
      <ConnectionsTabStrip
        core={core}
        tabs={tabs}
        activeTab={activeId}
        onSelect={select}
        ctxFor={(pid) => makePluginContext(pid, stores, theme)}
      />
      {/* Only the tab on show is mounted: one left is unmounted, and ends what it was doing. */}
      {activePlugin ? (
        <PluginPanelMount
          key={activePlugin.panel.id}
          ctx={makePluginContext(activePlugin.pluginId, stores, theme)}
          panel={activePlugin.panel}
        />
      ) : activeCore ? (
        <CoreTabContent key={activeCore.id} tab={activeCore.id} />
      ) : null}
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
