// FEA streaming: what the "Beams as solid" toggle may promise.
//
// The manifest saying a bake HAS beam solids is not the same as the viewer
// having BUILT them. The compact artefact is expanded through a wasm module the
// page fetches at runtime from /wasm/; a server (or a dev server) that does not
// serve it leaves the load to fail into a console warning, and a toggle gated
// on the manifest alone then flips `visible` on a mesh that does not exist.
// One rule, kept free of three.js and the loader so it can be tested.

import type {FeaManifest} from "@/services/viewerApi";

export interface BeamSolidsToggleState {
    /** Show the toggle at all: the bake wrote beam solids. */
    offered: boolean;
    /** The toggle can do something: they were built for this session. */
    enabled: boolean;
    /** Tooltip — what the toggle does, or why it cannot. */
    title: string;
}

const ENABLED_TITLE = "Draw beam elements as their solid cross-section, which also shows twist";

/** Does this manifest name a beam-solid artefact? */
export function manifestHasBeamSolids(manifest: FeaManifest | null | undefined): boolean {
    return !!(manifest?.mesh?.beam_solids_url || manifest?.mesh?.beam_solids_compact_url);
}

export function beamSolidsToggleState(
    manifest: FeaManifest | null | undefined,
    unavailable: string | null,
): BeamSolidsToggleState {
    const offered = manifestHasBeamSolids(manifest);
    if (!offered) return {offered, enabled: false, title: ENABLED_TITLE};
    if (unavailable) {
        return {offered, enabled: false, title: `Beam solids are not available: ${unavailable}`};
    }
    return {offered, enabled: true, title: ENABLED_TITLE};
}

/** Why the user turned them off, in their words. */
export const BEAM_SOLIDS_PERF_OPT_OUT =
    "skipped by \"Skip beam-solid load\" under Performance options; reload the result after turning it off";

/** One line for the tooltip from whatever the load threw. */
export function describeBeamSolidsFailure(err: unknown): string {
    const msg = err instanceof Error ? err.message : String(err);
    return `could not be built (${msg})`;
}
