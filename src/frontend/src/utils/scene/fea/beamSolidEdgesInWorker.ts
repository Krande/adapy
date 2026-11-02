// The beam-solid edges built in a worker. Imported lazily (beamSolidEdges.ts), so the inline
// worker is only evaluated once some solids want their edges, and never under a test runner.

import * as Comlink from "comlink";

import type {BeamSolidEdgeData} from "./beamSolidFeatureEdges";
import BeamSolidEdgesWorker from "./beamSolidEdges.worker.ts?worker&inline";
import type {BeamSolidEdgesWorkerAPI} from "./beamSolidEdges.worker";

/** `computeBeamSolidEdges` in a worker. The arrays are copied for it (the transfer would
 *  detach the caller's); throws when no worker can be spawned. */
export async function computeBeamSolidEdgesInWorker(
    positions: Float32Array,
    index: ArrayLike<number>,
    ranges: ReadonlyArray<[number, number]>,
): Promise<BeamSolidEdgeData> {
    const worker = new BeamSolidEdgesWorker();
    try {
        const api = Comlink.wrap<BeamSolidEdgesWorkerAPI>(worker);
        const input = {
            positions: new Float32Array(positions),
            // The constructor, not `from`: a typed-array source is a block copy, not an iteration.
            index: new Uint32Array(index),
            ranges: Uint32Array.from(ranges.flat()),
        };
        return await api.compute(
            Comlink.transfer(input, [input.positions.buffer, input.index.buffer, input.ranges.buffer] as ArrayBuffer[]),
        );
    } finally {
        worker.terminate();
    }
}
