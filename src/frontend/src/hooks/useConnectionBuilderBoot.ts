// The connection builder's boot.
//
// `ada dev connections`: on a viewer serving a connection library, start the
// builder's live loop and open the Scene panel on its Connection tab, once, at
// startup.
//
// A deployed studio: ask whether the current scope offers any specs (the
// workers' own, as the admin assigned them) once a scope is known -- which is
// after sign-in -- and on every scope change; the Connection tab shows while it
// does. The loop starts the first time somebody opens that tab, not at load:
// nobody pays for builds they did not ask for.
//
// The loop's first build is asked for as soon as the library reports in, which
// on a cold start is before ThreeCanvas has mounted its scene; a model loaded
// then is lost. So the scene's arrival is reported to the session, which holds
// its first load until it (as useUrlParamLoad holds a deep-linked file).

import { useEffect } from "react";

import { devConnectionsApi } from "@/services/api/devConnections";
import { scopeUrlPart, useScopeStore } from "@/state/scopeStore";
import { useConnectionsOfferStore } from "@/state/connectionsOfferStore";

import { runtime } from "@/runtime/config";
import { CORE_PANEL_IDS, reveal } from "@/layout/panelActions";
import { pollUntilTerminal } from "@/hooks/useJobPoll";
import {
  markConnectionBuilderSceneReady,
  startConnectionBuilderSession,
} from "@/services/devConnections/connectionBuilderSession";
import { useViewerRefs } from "@/state/AdaViewerContext";
import { useSceneInfoStore } from "@/state/sceneInfoStore";

const SCENE_WAIT_INTERVAL_MS = 100;
const SCENE_WAIT_MAX_MS = 15000;

export function useConnectionBuilderBoot(): void {
  const { scene: sceneRef } = useViewerRefs();
  useEffect(() => {
    if (!runtime.devConnections()) return startDeployed(() => sceneRef.current != null);
    startConnectionBuilderSession();
    useSceneInfoStore.getState().setMode("connection");
    reveal(CORE_PANEL_IDS.scene);
    void pollUntilTerminal({
      fetch: async () => sceneRef.current,
      terminal: (scene) => scene != null,
      fetchFirst: true,
      intervalMs: SCENE_WAIT_INTERVAL_MS,
      deadlineMs: SCENE_WAIT_MAX_MS,
    }).then(() => markConnectionBuilderSceneReady());
  }, [sceneRef]);
}

function startDeployed(sceneMounted: () => boolean): () => void {
  const check = async () => {
    const current = useScopeStore.getState().current;
    if (!current) return;
    try {
      const state = await devConnectionsApi.state();
      if (scopeUrlPart(useScopeStore.getState().current) === scopeUrlPart(current)) {
        useConnectionsOfferStore.getState().setOffer(state.specs.length);
      }
    } catch {
      // An older server without the route, or nothing reachable: nothing is offered.
      useConnectionsOfferStore.getState().setOffer(0);
    }
  };
  void check();
  const unsubscribeScope = useScopeStore.subscribe((s, prev) => {
    if (scopeUrlPart(s.current) !== scopeUrlPart(prev.current) || (s.current && !prev.current)) void check();
  });
  const startOnOpen = (mode: string) => {
    if (mode !== "connection") return false;
    startConnectionBuilderSession();
    if (sceneMounted()) markConnectionBuilderSceneReady();
    return true;
  };
  let unsubscribeMode = () => {};
  if (!startOnOpen(useSceneInfoStore.getState().mode)) {
    unsubscribeMode = useSceneInfoStore.subscribe((s) => {
      if (startOnOpen(s.mode)) unsubscribeMode();
    });
  }
  return () => {
    unsubscribeScope();
    unsubscribeMode();
  };
}
