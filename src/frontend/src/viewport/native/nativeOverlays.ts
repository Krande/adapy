// Overlay lines, points and shapes (viewport/api/overlayLayer.ts) on the three.js-free viewport: each one a
// geometry of the renderer, made through the scene's result layers with the look of an annotation
// (unlit, never picked, over the model or just in front of the faces it lies on). A move is written
// as the geometry's morph, so no geometry is made again while something follows the model.
//
// The renderer cannot change how many vertices a geometry has. A handle whose count changes
// (`setGeometry`) keeps the geometry while the new data fits in it: what is drawn is written as the
// morph, and the vertices beyond it are put on the last one that is drawn, where a segment has no
// length and a point lies under its twin. Only data that no longer fits makes a new, larger geometry.
import {colorsPerEndpoint, grownCapacity, segmentDistances, type OverlayLayer, type OverlayLines, type OverlayLook, type OverlayMesh, type OverlayPoints} from "@/viewport/api/overlayLayer";
import type {ResultMeshData} from "@/viewport/api/resultLayer";
import type {NativeResultLayer} from "./nativeResultLayer";
import type {NativeScene} from "./nativeScene";

interface Item {
    /** What it is made from; `positions` is where it was added (the morph's base). */
    data: ResultMeshData;
    /** A colour per vertex, when it has its own colours. */
    colors: Float32Array | null;
    /** How far it has been moved from where it was added, once it has been. */
    delta: Float32Array | null;
    layer: NativeResultLayer | null;
    /** Dashes as it was added with them: measured again when the geometry is. */
    dashed: OverlayLines["dashed"];
    visible: boolean;
    /** Nothing of it is drawn (a count of zero): hidden whatever its look says. */
    empty: boolean;
    /** A colour given after it was added, in place of the one it was made with. */
    color: readonly [number, number, number] | null;
}

export function createNativeOverlayLayer(scene: NativeScene): OverlayLayer {
    const items = new Map<number, Item>();
    let nextHandle = 1;
    let disposed = false;

    const draw = (item: Item): void => {
        try {
            const layer = scene.createResultLayer(item.data);
            if (item.colors) layer.setColors(item.colors);
            else if (item.color) layer.setColor(item.color);
            if (item.delta) {
                layer.setDisplacement(item.delta);
                layer.setInfluence(1);
            }
            if (!item.visible || item.empty) layer.setVisible(false);
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

    const measured = (dashed: OverlayLines["dashed"], positions: Float32Array) =>
        dashed ? {dash: dashed.dash, gap: dashed.gap, distances: segmentDistances(positions, new Float32Array(positions.length / 3))} : undefined;

    const look = (
        spec: OverlayLook, topology: ResultMeshData["topology"], positions: Float32Array, sizePx: number,
        dashed?: OverlayLines["dashed"], indices: Uint32Array | null = null,
    ): ResultMeshData => ({
        positions,
        indices,
        topology,
        matrix: spec.matrix ? Array.from(spec.matrix) : null,
        color: spec.color ?? [1, 1, 1],
        sizePx,
        opacity: spec.opacity,
        overlay: {onTop: !!spec.onTop, clipped: !!spec.clipped, dashed: measured(dashed, positions)},
    });

    const put = (data: ResultMeshData, colors: Float32Array | null, dashed?: OverlayLines["dashed"]): number => {
        const item: Item = {data, colors, delta: null, layer: null, dashed, visible: true, empty: false, color: null};
        // Painted colours are drawn times the layer's own: white under them.
        if (colors) item.data.color = [1, 1, 1];
        const handle = nextHandle++;
        items.set(handle, item);
        draw(item);
        return handle;
    };

    const release = (item: Item): void => {
        try {
            item.layer?.remove();
        } catch (err) {
            // The renderer may already have stopped (a lost device); there is nothing left to free.
            console.warn("[adacpp-native] releasing an overlay:", err);
        }
        item.layer = null;
    };

    const drop = (handle: number): void => {
        const item = items.get(handle);
        if (!item) return;
        items.delete(handle);
        release(item);
    };

    /** `used` numbers of `positions` as `item`'s geometry, in a new geometry with room for `capacity`
     *  vertices: the old one is taken off the renderer first, so nothing of it stays on the GPU. */
    const rebuild = (item: Item, positions: Float32Array, used: number, capacity: number): void => {
        release(item);
        const base = new Float32Array(capacity * 3);
        for (let i = 0; i < used; i++) base[i] = positions[i];
        for (let i = Math.max(used, 3); i < base.length; i++) base[i] = base[i - 3];
        item.data.positions = base;
        item.delta = null;
        if (item.colors) item.colors = new Float32Array(base.length).fill(1);
        if (item.data.overlay) item.data.overlay.dashed = measured(item.dashed, base);
    };

    return {
        addLines: (spec: OverlayLines) => {
            const positions = new Float32Array(spec.segments);
            const colors = spec.colors ? colorsPerEndpoint(spec.colors, new Float32Array(positions.length)) : null;
            return put(look(spec, "lines", positions, spec.widthPx ?? 2, spec.dashed), colors, spec.dashed);
        },
        addPoints: (spec: OverlayPoints) =>
            put(look(spec, "points", new Float32Array(spec.positions), spec.sizePx ?? 6), spec.colors ? new Float32Array(spec.colors) : null),
        addMesh: (spec: OverlayMesh) =>
            put(
                look(spec, "triangles", new Float32Array(spec.positions), 1, undefined, new Uint32Array(spec.indices)),
                spec.colors ? new Float32Array(spec.colors) : null,
            ),
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
        setGeometry: (handle, positions, count, colors) => {
            const item = items.get(handle);
            if (!item || item.data.topology === "triangles") return;
            const lines = item.data.topology === "lines";
            const stride = lines ? 6 : 3;
            const n = Math.max(0, Math.min(count ?? Infinity, Math.floor(positions.length / stride)));
            const used = n * stride;
            const held = item.data.positions.length;
            const wasEmpty = item.empty;
            item.empty = n === 0;
            // Dashes are measured along the geometry as it is made: made again, with exactly what is drawn.
            const remake = used > held || (!!item.dashed && n > 0);
            if (remake) {
                const vertices = item.dashed ? used / 3 : grownCapacity(held / 3, used / 3);
                rebuild(item, positions, used, Math.max(vertices, lines ? 2 : 1));
            }
            const base = item.data.positions;
            if (!remake && n > 0) {
                // In the geometry it has: what is drawn as the morph, the rest on the last vertex drawn.
                const delta = (item.delta ??= new Float32Array(base.length));
                for (let i = 0; i < used; i++) delta[i] = positions[i] - base[i];
                for (let i = used; i < base.length; i++) delta[i] = positions[used - 3 + (i % 3)] - base[i];
            }
            if (item.colors && colors && n > 0) {
                const tint = item.colors;
                if (lines) {
                    for (let s = 0, o = 0; s < n * 3; s += 3, o += 6) {
                        tint[o] = tint[o + 3] = colors[s];
                        tint[o + 1] = tint[o + 4] = colors[s + 1];
                        tint[o + 2] = tint[o + 5] = colors[s + 2];
                    }
                } else {
                    for (let i = 0; i < used; i++) tint[i] = colors[i];
                }
                // The vertices beyond what is drawn take the last colour, as they take the last place.
                for (let i = used; i < tint.length; i++) tint[i] = tint[i - 3];
            }
            if (remake) {
                draw(item);
                return;
            }
            const layer = item.layer;
            if (!layer) return;
            if (n > 0) {
                layer.setDisplacement(item.delta!);
                layer.setInfluence(1);
                if (item.colors && colors) layer.setColors(item.colors);
            }
            if (item.empty !== wasEmpty) layer.setVisible(item.visible && !item.empty);
        },
        setLook: (handle, change) => {
            const item = items.get(handle);
            if (!item) return;
            const layer = item.layer;
            // Kept on the item too: what it is made from again after a load.
            if (change.visible !== undefined) {
                item.visible = change.visible;
                layer?.setVisible(item.visible && !item.empty);
            }
            if (change.opacity !== undefined) {
                item.data.opacity = change.opacity;
                layer?.setOpacity(change.opacity);
            }
            if (change.color && !item.colors) {
                item.color = [change.color[0], change.color[1], change.color[2]];
                layer?.setColor(item.color);
            }
            if (change.matrix !== undefined) {
                item.data.matrix = change.matrix ? Array.from(change.matrix) : null;
                layer?.setMatrix(item.data.matrix ?? null);
            }
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
