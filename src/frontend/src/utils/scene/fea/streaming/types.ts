// FEA streaming: the loader's contract.
//
// Owns: the argument type of `load_fea_streaming`, so a module that asks for
// a load (the defaults entry point, the step callback) can name it without
// importing the loader.

import type {FeaManifest} from "@/services/viewerApi";

/** What one streaming-FEA load is asked for. */
export interface LoadFeaStreamingArgs {
    sourceName: string;
    manifest: FeaManifest;
    /** null = field-less mesh (design-model FEM): load mesh + beam-solids only, no result
     *  coloring / warp / step animation. */
    fieldName: string | null;
    stepIndex: number;
    reduction: string | null;
    displacementScale?: number;
    /**
     * The sweep slider's own position, when the caller is starting a session and
     * wants the slider moved to it.
     *
     * Distinct from ``displacementScale`` on purpose. That one is the MORPH
     * INFLUENCE — the slider position multiplied by the warp-scale knob — and
     * writing it back into the slider was how selecting a component moved the
     * indicator without moving the model: at a warp scale of 0.2 a slider on 0.55
     * sends an influence of 0.11, the slider then read 0.11, and the shape did not
     * change because the influence had not. Compounding, too: the next selection
     * would have sent 0.022.
     *
     * Omit it and the slider is left where the user put it, which is what every
     * re-apply wants — component, step, layer, colormap. Only a fresh load passes
     * it.
     */
    sliderFactor?: number;
    /** Colormap ID — one of the keys in ``COLORMAPS``. Optional so
     * existing call-sites that don't care still work; we fall back to
     * the active store value (and from there to viridis if unset). */
    colormap?: string;
    /** Optional stage reporter so the toast can show mesh-load /
     *  render progress, not just the manifest poll. ``progress`` is
     *  a fraction in [0, 1] over the load_fea_streaming portion of
     *  the flow; the caller is responsible for remapping that into
     *  the wider queue+convert+load progress bar. */
    onStage?: (stage: string, progress: number) => void;
    /** Optional abort signal — checked between async stages so the
     *  user clicking Kill in the toast bails out without waiting for
     *  the in-flight fetch (which doesn't itself accept a signal). */
    signal?: AbortSignal;
}

/** A function that performs one streaming-FEA load. */
export type LoadFeaStreaming = (args: LoadFeaStreamingArgs) => Promise<void>;
