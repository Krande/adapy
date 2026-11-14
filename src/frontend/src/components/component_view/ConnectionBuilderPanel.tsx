// The connection builder as a panel of its own (`core:component-controls`, the top bar's
// connection button): the same builder as the Scene panel's Connection tab -- pick a spec, edit
// each member, see the joint rebuilt -- in the menu column on desktop and a bottom sheet on phones.
//
// It replaced the "Connection components" panel, which offered only the specs a CI bake had
// published to a scope. The builder's specs are the ones the live workers' code registers (or, on
// `ada dev connections`, the served library's), offered per scope as the admin assigned them.

import React, { useEffect } from "react";

import { SimulationsFrame } from "@/components/simulations/SimulationsFrame";
import { PanelHeaderActions, usePanelHeaderActions } from "@/components/ui/PanelFrame";
import {
  markConnectionBuilderSceneReady,
  startConnectionBuilderSession,
} from "@/services/devConnections/connectionBuilderSession";
import { useComponentControlsStore } from "@/state/componentControlsStore";

import { ConnectionBuilderBody } from "./ConnectionBuilder";

export const CONNECTION_BUILDER_TITLE = "Connection builder";

/** The builder's content; opening it starts the builder's loop (once per page). */
export const ConnectionBuilderPanelContent: React.FC = () => {
  useEffect(() => {
    startConnectionBuilderSession();
    // The panel opens on a mounted viewer: the scene is there for the first build to load into.
    markConnectionBuilderSceneReady();
  }, []);
  return <ConnectionBuilderBody />;
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
