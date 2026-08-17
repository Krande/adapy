// Simulation follower route. A window opened with `?simfollow=<source>&panel=<id>`
// boots this canvas-less page instead of the full viewer: no 3D scene, no tree —
// just the Simulation panel's plugin tab mounted full-window (SimWindowFrame in
// "window" mode). The big window hosts the ENTIRE control surface and drives the
// original tab's 3D scene over the `ada-sim` BroadcastChannel (the plugin's own
// channel bridge echoes user actions back and applies the viewer's state here).
//
// To host the full controls the follower loads the same result sidecars the
// viewer tab loads on model open — both tabs share the session + storage, so it
// re-runs the plugin sidecar loaders against the source's derived dir. It never
// fetches geometry or the FEA manifest (which could trigger a conversion); a
// plugin loader that needs no manifest (one that probes its own sidecar)
// populates fine from the fetcher alone.

import React from "react";

import SimulationControls from "@/components/simulation/SimulationControls";
import {followerParams} from "@/utils/simChannel";
import {scopeFromUrlPart, useScopeStore} from "@/state/scopeStore";
import {runResultSidecarLoaders, type SidecarFetcher} from "@/plugins";
import {makeViewerApiFetcher} from "@/services/feaFieldBlob";
import {viewerApi} from "@/services/viewerApi";

const SimFollowerPage: React.FC = () => {
  const follow = React.useMemo(() => followerParams(), []);

  React.useEffect(() => {
    if (!follow) return;
    // Adopt the driving tab's scope so plugin actions (e.g. enqueuing a check)
    // run against the same scope the viewer is in.
    useScopeStore.getState().setCurrent(scopeFromUrlPart(follow.scope));

    let cancelled = false;
    void (async () => {
      try {
        const {scope, source} = follow;
        const {fetcher, rangeFetcher} = makeViewerApiFetcher(scope, source);
        const feaPrefix = `_derived/${source.replace(/^\/+/, "")}.fea/`;
        const sidecar: SidecarFetcher = {
          url: (rel) => viewerApi.blobUrl(scope, feaPrefix + rel.replace(/^\/+/, "")),
          json: async (rel) =>
            JSON.parse(new TextDecoder().decode(new Uint8Array(await fetcher(rel)))),
          bytes: async (rel, range) => {
            if (!range) return fetcher(rel);
            const res = await rangeFetcher(rel, range.start, range.end);
            // getBlobRange returns {buf, ranged} on a 206 (or a whole-object
            // fallback); SidecarFetcher wants the raw ArrayBuffer.
            return res instanceof ArrayBuffer ? res : res.buf;
          },
        };
        // No geometry manifest in a follower — pass an empty one. Sidecar loaders
        // that key off manifest.plugins self-skip; probing loaders still run.
        await runResultSidecarLoaders({manifest: {}, fetcher: sidecar, scope, sourceName: source});
        if (cancelled) return;
      } catch (err) {
        // eslint-disable-next-line no-console
        console.warn("[sim-follower] sidecar self-load failed (non-fatal)", err);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [follow]);

  if (!follow) return null;

  return (
    <div className="h-[100dvh] w-full overflow-hidden bg-[var(--ada-panel-bg)]">
      <SimulationControls initialMode="window" forcedTabId={follow.panel} />
    </div>
  );
};

export default SimFollowerPage;
