// The in-browser beam-solid expansion: the adacpp_extrude WebAssembly module, run in a
// worker. What the REST viewer's compute capability answers `expandBeamSolids` with (the
// websocket one asks the Python process instead -- see services/capabilities/types.ts).
//
// Imported lazily by the capability, so nothing here (the inline worker, the module
// loader) is evaluated until a compact beam-solid artefact actually needs expanding.

import {expandBeamSolidsFromBytes, type ExpandedBeamSolids} from "@/services/feaBeamSolidsCompact";
// Inline-bundled worker — Vite handles the import + URL plumbing.
import BeamSolidsExpandWorker from "@/utils/scene/fea/streaming/beamSolidsExpand.worker.ts?worker&inline";
import type {BeamSolidsExpandWorkerAPI} from "@/utils/scene/fea/streaming/beamSolidsExpand.worker";

/** Run the expansion in a worker; fall back to the main thread when one
 *  cannot be spawned (a test runner, a hardened embed, an OOM). The fallback
 *  calls the very same function the worker does, so the only difference
 *  between them is which thread pays for it. */
export async function expandBeamSolidsInBrowser(
    compact: ArrayBuffer,
    mainPositions: Float32Array,
): Promise<ExpandedBeamSolids> {
    let worker: Worker | null = null;
    try {
        worker = new BeamSolidsExpandWorker();
    } catch (err) {
        // eslint-disable-next-line no-console
        console.warn("[fea-streaming] beam-solid expand worker unavailable:", err);
        return expandBeamSolidsFromBytes(compact, mainPositions);
    }
    try {
        const Comlink = await import("comlink");
        const api = Comlink.wrap<BeamSolidsExpandWorkerAPI>(worker);
        // ``mainPositions`` is a snapshot copy already, but copy again: the
        // session keeps its snapshot for the whole load, and transferring
        // would detach it.
        const positionsCopy = new Float32Array(mainPositions);
        return await api.expand(
            Comlink.transfer(
                {compact, mainPositions: positionsCopy},
                [compact, positionsCopy.buffer as ArrayBuffer],
            ),
        );
    } finally {
        worker.terminate();
    }
}
