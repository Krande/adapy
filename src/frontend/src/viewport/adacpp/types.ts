// The embind surface of adacpp_render.wasm (adacpp src/render/wasm_bindings.cpp). Pointers are byte
// addresses into the module heap; the caller owns and frees them.

export interface AdacppStats {
    geometries: number;
    instances: number;
    clustersTotal: number;
    clustersDrawn: number;
    trianglesTotal: number;
    trianglesDrawn: number;
    /** Clusters dropped by the occlusion test (GPU culling only). */
    clustersOccluded: number;
    /** Culled on the GPU (two-pass occlusion, large scenes): drawn / occluded counts are read back
     *  asynchronously via readCullStats; stats() has them as zero. */
    gpuCulled: boolean;
    drawCalls: number;
    cullMs: number;
    /** Time spent mirroring the three.js scene into the renderer this frame (JS side). */
    syncMs?: number;
}

export interface WebRendererApi {
    resize(width: number, height: number): void;
    render(): void;
    addGeometry(
        topology: number,
        positions: number,
        vertexCount: number,
        normals: number,
        colors: number,
        morph: number,
        indices: number,
        indexCount: number,
        segments: number,
        segmentCount: number,
    ): number;
    updateColors(geometry: number, colors: number, count: number): void;
    updateMorph(geometry: number, morph: number, vertexCount: number): void;
    removeGeometry(geometry: number): void;
    segmentCount(geometry: number): number;
    addInstance(geometry: number): number;
    setInstance(
        instance: number,
        matrix: number,
        r: number,
        g: number,
        b: number,
        a: number,
        flags: number,
        morphInfluence: number,
        metalness: number,
        roughness: number,
        specular: boolean,
    ): void;
    setSegmentStates(instance: number, packed: number, count: number): void;
    removeInstance(instance: number): void;
    clear(): void;
    setCamera(view: number, proj: number, orthographic: boolean): void;
    setBackground(r: number, g: number, b: number): void;
    setLighting(ambient: number, diffuse: number): void;
    setCulling(frustum: boolean, cone: boolean, occlusion: boolean): void;
    pick(x: number, y: number, width: number, height: number, callback: (r: RawPick) => void): void;
    stats(): AdacppStats;
    readCullStats(callback: (s: AdacppStats) => void): void;
    delete(): void;
}

/** What the renderer's GPU pick found under a pixel. */
export interface RawPick {
    hit: boolean;
    instance: number;
    segment: number;
    /** Triangle index in the geometry's original index list (index position / 3). */
    triangle: number;
    world: [number, number, number];
}

export interface AdacppModule {
    WebRenderer: new (canvasSelector: string, format: string) => WebRendererApi;
    HEAPU8: Uint8Array;
    HEAPU32: Uint32Array;
    HEAPF32: Float32Array;
    _malloc(bytes: number): number;
    _free(ptr: number): void;
}

// The slice of the WebGPU DOM API this module touches. TypeScript's lib.dom does not ship WebGPU
// and the frontend has no @webgpu/types; the device itself is only handed to the wasm module.
export interface GpuAdapterInfoLike {
    vendor?: string;
    architecture?: string;
    description?: string;
}
export interface GpuDeviceLike {
    readonly lost: Promise<{message: string}>;
    onuncapturederror: ((ev: {error: {message: string}}) => void) | null;
    destroy(): void;
}
export interface GpuAdapterLike {
    readonly limits: {maxStorageBufferBindingSize: number; maxBufferSize: number};
    readonly info?: GpuAdapterInfoLike;
    requestDevice(desc: {label?: string; requiredLimits?: Record<string, number>}): Promise<GpuDeviceLike>;
}
export interface GpuLike {
    requestAdapter(opts?: {powerPreference?: "low-power" | "high-performance"}): Promise<GpuAdapterLike | null>;
    getPreferredCanvasFormat(): string;
}

// Keep in step with adacpp src/render/renderer.h.
export const Topology = {Triangles: 0, Lines: 1, Points: 2} as const;
export const InstanceFlag = {
    VertexColors: 1 << 0,
    Unlit: 1 << 1,
    DoubleSided: 1 << 2,
    Transparent: 1 << 3,
    Hidden: 1 << 4,
    MorphRelative: 1 << 5,
    NoPick: 1 << 8,
} as const;
export const SegmentFlag = {Hidden: 1 << 0} as const;
