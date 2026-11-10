// The result layer (viewport/api/resultLayer.ts) over the adacpp WebGPU renderer: one geometry with
// per-vertex colours and a morph (the displacement), one instance whose morph influence is the
// deformation scale. The same renderer calls the three.js scene mirror makes for a result mesh
// (viewport/adacpp/sceneSync.ts), without a three.js object behind them. No three.js in this module.
import type {ResultMeshData, ResultMeshLayer} from "@/viewport/api/resultLayer";
import {InstanceFlag, Topology, type AdacppModule, type WebRendererApi} from "@/viewport/adacpp/types";

const WHITE = 0xffffffff;
const IDENTITY = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1];
const TOPOLOGY = {triangles: Topology.Triangles, lines: Topology.Lines, points: Topology.Points} as const;

export class NativeResultLayer implements ResultMeshLayer {
    readonly vertexCount: number;
    private geometry: number;
    private instance: number;
    private alive = true;
    // Kept on the module heap for the layer's life, so a step or a frame allocates nothing: packed
    // RGBA8 per vertex, xyz per vertex, and the instance's matrix.
    private readonly colorsPtr: number;
    private readonly morphPtr: number;
    private readonly matrixPtr: number;
    private readonly color: readonly [number, number, number];
    private readonly baseFlags: number;
    /** Lines' width and points' diameter in the drawing buffer's pixels, and the layer's opacity. */
    private readonly size: number;
    private readonly opacity: number;
    /** Dashed lines: the dash and gap lengths; 0 draws them solid. */
    private dash = 0;
    private gap = 0;
    private painted = false;
    private colorsVisible = true;
    private visible = true;
    private influence = 0;

    constructor(
        private readonly module: AdacppModule,
        private readonly renderer: WebRendererApi,
        data: ResultMeshData,
        /** The picture is out of date: the scene draws a frame. */
        private readonly changed: () => void,
        /** CSS pixels to the drawing buffer's. */
        pixelRatio = 1,
        /** The index list cut into segments (start and count, two numbers each, covering it in
         *  order): each is picked, hidden and coloured on its own. */
        segments: Uint32Array | null = null,
    ) {
        const M = module;
        const n = data.positions.length / 3;
        this.vertexCount = n;
        const triangles = data.topology === "triangles";
        this.color = data.color ?? (triangles ? [1, 1, 1] : [0, 0, 0]);
        // Faces: both sides (a shell has no inside), cut by the section planes, and just behind
        // coplanar lines so element edges draw cleanly on them. Lines and points are unlit overlays
        // that a click passes through.
        this.opacity = Math.max(0, Math.min(1, data.opacity ?? 1));
        this.size = Math.max(1, (data.sizePx ?? 1) * pixelRatio);
        const overlay = data.overlay;
        if (overlay) {
            // An annotation: unlit, a click passes through it, and it wins over the faces it lies on.
            this.baseFlags = InstanceFlag.MorphRelative | InstanceFlag.Unlit | InstanceFlag.NoPick |
                (triangles ? InstanceFlag.DoubleSided : 0) |
                (overlay.onTop ? InstanceFlag.OnTop : InstanceFlag.DepthOffset) |
                (overlay.clipped === false ? 0 : InstanceFlag.Clipped) |
                (this.opacity < 1 ? InstanceFlag.Transparent : 0);
            this.dash = overlay.dashed?.dash ?? 0;
            this.gap = overlay.dashed?.gap ?? 0;
        } else {
            this.baseFlags = InstanceFlag.MorphRelative | InstanceFlag.Clipped |
                (triangles ? InstanceFlag.DoubleSided | InstanceFlag.DepthBack : InstanceFlag.Unlit | InstanceFlag.NoPick) |
                (this.opacity < 1 ? InstanceFlag.Transparent : 0);
        }

        this.colorsPtr = M._malloc(Math.max(1, n) * 4);
        this.morphPtr = M._malloc(Math.max(1, n) * 12);
        this.matrixPtr = M._malloc(64);
        M.HEAPU32.fill(WHITE, this.colorsPtr >> 2, (this.colorsPtr >> 2) + n);
        M.HEAPF32.fill(0, this.morphPtr >> 2, (this.morphPtr >> 2) + n * 3);
        M.HEAPF32.set(data.matrix ?? IDENTITY, this.matrixPtr >> 2);

        const positionsPtr = M._malloc(Math.max(1, n) * 12);
        const indexCount = data.indices?.length ?? 0;
        const indicesPtr = indexCount ? M._malloc(indexCount * 4) : 0;
        // The parts of the index list that are picked and given a state one at a time.
        const segmentCount = segments ? segments.length / 2 : 0;
        const segmentsPtr = segmentCount ? M._malloc(segments!.byteLength) : 0;
        const distances = this.dash > 0 ? data.overlay!.dashed!.distances : null;
        const distancesPtr = distances ? M._malloc(Math.max(1, n) * 4) : 0;
        try {
            if (distancesPtr) M.HEAPF32.set(distances!.length === n ? distances! : distances!.subarray(0, n), distancesPtr >> 2);
            M.HEAPF32.set(data.positions, positionsPtr >> 2);
            if (indicesPtr) M.HEAPU32.set(data.indices!, indicesPtr >> 2);
            if (segmentsPtr) M.HEAPU32.set(segments!, segmentsPtr >> 2);
            // No normals: the renderer shades each face by its own, which follows the deformed shape.
            this.geometry = renderer.addGeometry(
                TOPOLOGY[data.topology], positionsPtr, n, 0, this.colorsPtr, this.morphPtr, indicesPtr, indexCount,
                segmentsPtr, segmentCount, distancesPtr,
            );
        } finally {
            if (distancesPtr) M._free(distancesPtr);
            M._free(positionsPtr);
            if (indicesPtr) M._free(indicesPtr);
            if (segmentsPtr) M._free(segmentsPtr);
        }
        this.instance = renderer.addInstance(this.geometry);
        this.push();
    }

    /** The instance's state: its matrix, colour, flags and the morph influence. */
    private push(): void {
        if (!this.alive) return;
        let flags = this.baseFlags;
        if (this.painted && this.colorsVisible) flags |= InstanceFlag.VertexColors;
        if (!this.visible) flags |= InstanceFlag.Hidden;
        const [r, g, b] = this.color;
        this.renderer.setInstance(this.instance, this.matrixPtr, r, g, b, this.opacity, flags, this.influence, 0, 1, false, this.size, this.dash, this.gap);
        this.changed();
    }

    setColors(rgb: Float32Array): void {
        if (!this.alive) return;
        // Packed straight into the heap (linear RGBA8, as the renderer stores vertex colours). The
        // view is taken now: the heap may have grown since the layer was made.
        const out = this.module.HEAPU32;
        const at = this.colorsPtr >> 2;
        const n = Math.min(this.vertexCount, Math.floor(rgb.length / 3));
        for (let v = 0, s = 0; v < n; v++, s += 3) {
            // Colour maps write channels in 0..1; `+ 0.5 | 0` rounds, and a stray value is clamped.
            const r = rgb[s] * 255 + 0.5, g = rgb[s + 1] * 255 + 0.5, b = rgb[s + 2] * 255 + 0.5;
            out[at + v] =
                ((r < 0 ? 0 : r > 255 ? 255 : r | 0) | ((g < 0 ? 0 : g > 255 ? 255 : g | 0) << 8) |
                    ((b < 0 ? 0 : b > 255 ? 255 : b | 0) << 16) | 0xff000000) >>> 0;
        }
        this.renderer.updateColors(this.geometry, this.colorsPtr, this.vertexCount);
        if (!this.painted) {
            this.painted = true;
            this.push();
        } else {
            this.changed();
        }
    }

    setDisplacement(xyz: Float32Array): void {
        if (!this.alive) return;
        const n = this.vertexCount * 3;
        this.module.HEAPF32.set(xyz.length === n ? xyz : xyz.subarray(0, n), this.morphPtr >> 2);
        this.renderer.updateMorph(this.geometry, this.morphPtr, this.vertexCount);
        this.changed();
    }

    setInfluence(influence: number): void {
        if (!Number.isFinite(influence) || influence === this.influence) return;
        this.influence = influence;
        this.push();
    }

    setColorsVisible(visible: boolean): void {
        if (visible === this.colorsVisible) return;
        this.colorsVisible = visible;
        this.push();
    }

    setVisible(visible: boolean): void {
        if (visible === this.visible) return;
        this.visible = visible;
        this.push();
    }

    /** The renderer's instance of the layer: what a pick names. */
    get instanceId(): number {
        return this.instance;
    }

    /** Called when the layer is taken off the viewport by `remove`. */
    onRemove: (() => void) | null = null;

    remove(): void {
        if (!this.alive) return;
        this.renderer.removeInstance(this.instance);
        this.renderer.removeGeometry(this.geometry);
        this.detach();
        this.onRemove?.();
        this.changed();
    }

    /** The renderer dropped everything it drew (a new model, or it is going away): the layer's
     *  geometry is gone with it, and every later call does nothing. */
    detach(): void {
        if (!this.alive) return;
        this.alive = false;
        this.module._free(this.colorsPtr);
        this.module._free(this.morphPtr);
        this.module._free(this.matrixPtr);
    }
}
