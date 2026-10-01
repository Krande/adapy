// The edge overlay, built off the main thread. Welding and hashing every triangle of a large
// merged mesh is seconds of CPU; on the main thread that was the viewer freezing once per model.
import * as Comlink from "comlink";

import { computeEdgeArrays } from "./edgeCore";

const api = {
  build(
    pos: Float32Array,
    index: Uint16Array | Uint32Array,
    ranges: [number, number][],
    thresholdDot: number,
  ) {
    const out = computeEdgeArrays(pos, index, ranges, thresholdDot);
    // Handed back, not copied: the arrays are this worker's to give away.
    return Comlink.transfer(out, [out.positions.buffer, out.rangeIdx.buffer]);
  },
};

export type EdgeBuildApi = typeof api;

Comlink.expose(api);
