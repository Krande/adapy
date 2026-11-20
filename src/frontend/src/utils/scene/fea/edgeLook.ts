// The colour an FEA result's element edges are drawn in, from the appearance settings: the edge
// colour (white unless chosen) for the shell grid and the beam solids' edges, the beam elements'
// own dimmer grey for theirs, and both times the edge brightness. Both viewports draw edges in
// these colours. No three.js in this module.

import {useAppearanceStore, type AppearanceSettings} from "@/state/appearanceStore";
import {linearChannel} from "@/utils/scene/sceneColor";
import {FEA_EDGE_COLOR} from "./edgeColors";

type EdgeSettings = Pick<AppearanceSettings, "edgeColor" | "edgeBrightness">;

/** The sRGB channels (0..1) an edge drawn as `base` (an edgeColors.ts colour, 0xrrggbb) is
 *  drawn in now: FEA_EDGE_COLOR is the chosen edge colour; any other is itself. Times the
 *  brightness, on the sRGB value, so 0.5 is a line half as bright on screen. */
export function feaEdgeSrgb(base: number, s: EdgeSettings = useAppearanceStore.getState()): [number, number, number] {
    const hex = base === FEA_EDGE_COLOR ? parseInt(s.edgeColor.slice(1), 16) : base;
    const k = s.edgeBrightness;
    return [(((hex >> 16) & 255) / 255) * k, (((hex >> 8) & 255) / 255) * k, ((hex & 255) / 255) * k];
}

/** `feaEdgeSrgb` as linear RGB: what a viewport draws with. */
export function feaEdgeLinear(base: number, s: EdgeSettings = useAppearanceStore.getState()): [number, number, number] {
    const [r, g, b] = feaEdgeSrgb(base, s);
    return [linearChannel(r), linearChannel(g), linearChannel(b)];
}

/** Call `fn` whenever the edge colour or brightness changes; returns the unsubscribe. */
export function onFeaEdgeLookChange(fn: () => void): () => void {
    return useAppearanceStore.subscribe((now, before) => {
        if (now.edgeColor !== before.edgeColor || now.edgeBrightness !== before.edgeBrightness) fn();
    });
}
