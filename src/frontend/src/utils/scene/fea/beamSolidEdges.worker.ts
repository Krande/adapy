// Worker that builds the beam-solid element edges off the main thread (see
// beamSolidFeatureEdges.ts). The inputs are copies, transferred in; the outputs are
// transferred back.

import * as Comlink from "comlink";

import {computeBeamSolidEdges, type BeamSolidEdgeData} from "./beamSolidFeatureEdges";

export interface BeamSolidEdgesInput {
    positions: Float32Array;
    index: Uint32Array;
    /** `[start, count]` per range, flat. */
    ranges: Uint32Array;
}

const api = {
    compute(input: BeamSolidEdgesInput): BeamSolidEdgeData {
        const ranges: [number, number][] = [];
        for (let i = 0; i + 1 < input.ranges.length; i += 2) ranges.push([input.ranges[i], input.ranges[i + 1]]);
        const out = computeBeamSolidEdges(input.positions, input.index, ranges);
        return Comlink.transfer(out, [out.pairs.buffer as ArrayBuffer, out.owner.buffer as ArrayBuffer]);
    },
};

export type BeamSolidEdgesWorkerAPI = typeof api;

Comlink.expose(api);
