// `ada dev connections`: on a viewer serving a connection library, start the
// builder's live loop and open its panel once, at startup. Elsewhere a no-op.
//
// The loop's first build is asked for as soon as the library reports in, which
// on a cold start is before ThreeCanvas has mounted its scene; a model loaded
// then is lost. So the scene's arrival is reported to the session, which holds
// its first load until it (as useUrlParamLoad holds a deep-linked file).

import { useEffect } from "react";

import { runtime } from "@/runtime/config";
import { CORE_PANEL_IDS, reveal } from "@/layout/panelActions";
import { pollUntilTerminal } from "@/hooks/useJobPoll";
import {
  markConnectionBuilderSceneReady,
  startConnectionBuilderSession,
} from "@/services/devConnections/connectionBuilderSession";
import { useViewerRefs } from "@/state/AdaViewerContext";

const SCENE_WAIT_INTERVAL_MS = 100;
const SCENE_WAIT_MAX_MS = 15000;

export function useConnectionBuilderBoot(): void {
  const { scene: sceneRef } = useViewerRefs();
  useEffect(() => {
    if (!runtime.devConnections()) return;
    startConnectionBuilderSession();
    reveal(CORE_PANEL_IDS.connectionBuilder);
    void pollUntilTerminal({
      fetch: async () => sceneRef.current,
      terminal: (scene) => scene != null,
      fetchFirst: true,
      intervalMs: SCENE_WAIT_INTERVAL_MS,
      deadlineMs: SCENE_WAIT_MAX_MS,
    }).then(() => markConnectionBuilderSceneReady());
  }, [sceneRef]);
}
