// `ada dev connections`: on a viewer serving a connection library, start the
// builder's live loop and open its panel once, at startup. Elsewhere a no-op.

import { useEffect } from "react";

import { runtime } from "@/runtime/config";
import { CORE_PANEL_IDS, reveal } from "@/layout/panelActions";
import { startConnectionBuilderSession } from "@/services/devConnections/connectionBuilderSession";

export function useConnectionBuilderBoot(): void {
  useEffect(() => {
    if (!runtime.devConnections()) return;
    startConnectionBuilderSession();
    reveal(CORE_PANEL_IDS.connectionBuilder);
  }, []);
}
