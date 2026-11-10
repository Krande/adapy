// Overlay lines and points (viewport/api/overlayLayer.ts) on the three.js-free viewport: each one a
// geometry of the renderer, made through the scene's result layers with the look of an annotation
// (unlit, never picked, over the model or just in front of the faces it lies on). A move is written
// as the geometry's morph, so no geometry is made again while something follows the model.
import {colorsPerEndpoint, segmentDistances, type OverlayLayer, type OverlayLines, type OverlayLook, type OverlayPoints} from "@/viewport/api/overlayLayer";
import type {ResultMeshData, ResultMeshLayer} from "@/viewport/api/resultLayer";
import type {NativeScene} from "./nativeScene";

interface Item {
    /** What it is made from; `positions` is where it was added (the morph's base). */
    data: ResultMeshData;
    /** A colour per vertex, when it has its own colours. */
    colors: Float32Array | null;
    /** How far it has been moved from where it was added, once it has been. */
    delta: Float32Array | null;
    layer: ResultMeshLayer | null;
}

export function createNativeOverlayLayer(scene: NativeScene): OverlayLayer {
    const items = new Map<number, Item>();
    let nextHandle = 1;
    let disposed = false;

    const draw = (item: Item): void => {
        try {
            const layer = scene.createResultLayer(item.data);
            if (item.colors) layer.setColors(item.colors);
            if (item.delta) {
                layer.setDisplacement(item.delta);
                layer.setInfluence(1);
            }
            item.layer = layer;
        } catch (err) {
            // An annotation that cannot be drawn must not take the feature that asked for it down.
            console.warn("[adacpp-native] an overlay could not be drawn:", err);
            item.layer = null;
        }
    };

    // A load that empties the renderer's scene takes what was drawn here with it: make it again.
    let epoch = scene.sceneEpoch;
    const afterLoad = (): void => {
        if (disposed || epoch === scene.sceneEpoch) return;
        epoch = scene.sceneEpoch;
        for (const item of items.values()) draw(item);
    };
    scene.onLoaded.add(afterLoad);

    const look = (spec: OverlayLook, topology: "lines" | "points", positions: Float32Array, sizePx: number, dashed?: OverlayLines["dashed"]): ResultMeshData => ({
        positions,
        indices: null,
        topology,
        matrix: spec.matrix ? Array.from(spec.matrix) : null,
        color: spec.color ?? [1, 1, 1],
        sizePx,
        opacity: spec.opacity,
        overlay: {
            onTop: !!spec.onTop,
            clipped: !!spec.clipped,
            dashed: dashed ? {dash: dashed.dash, gap: dashed.gap, distances: segmentDistances(positions, new Float32Array(positions.length / 3))} : undefined,
        },
    });

    const put = (data: ResultMeshData, colors: Float32Array | null): number => {
        const item: Item = {data, colors, delta: null, layer: null};
        // Painted colours are drawn times the layer's own: white under them.
        if (colors) item.data.color = [1, 1, 1];
        const handle = nextHandle++;
        items.set(handle, item);
        draw(item);
        return handle;
    };

    const drop = (handle: number): void => {
        const item = items.get(handle);
        if (!item) return;
        items.delete(handle);
        try {
            item.layer?.remove();
        } catch (err) {
            // The renderer may already have stopped (a lost device); there is nothing left to free.
            console.warn("[adacpp-native] releasing an overlay:", err);
        }
    };

    return {
        addLines: (spec: OverlayLines) => {
            const positions = new Float32Array(spec.segments);
            const colors = spec.colors ? colorsPerEndpoint(spec.colors, new Float32Array(positions.length)) : null;
            return put(look(spec, "lines", positions, spec.widthPx ?? 2, spec.dashed), colors);
        },
        addPoints: (spec: OverlayPoints) =>
            put(look(spec, "points", new Float32Array(spec.positions), spec.sizePx ?? 6), spec.colors ? new Float32Array(spec.colors) : null),
        setPositions: (handle, positions) => {
            const item = items.get(handle);
            if (!item) return;
            const base = item.data.positions;
            const n = Math.min(base.length, positions.length);
            const delta = (item.delta ??= new Float32Array(base.length));
            for (let i = 0; i < n; i++) delta[i] = positions[i] - base[i];
            item.layer?.setDisplacement(delta);
            item.layer?.setInfluence(1);
        },
        remove: drop,
        clear: () => {
            for (const handle of [...items.keys()]) drop(handle);
        },
        dispose: () => {
            for (const handle of [...items.keys()]) drop(handle);
            disposed = true;
            scene.onLoaded.delete(afterLoad);
        },
    };
}
