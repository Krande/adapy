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

/** loadGlb's options (adacpp glb_scene.h GlbOptions); all optional. Colours are linear RGBA. */
export interface LoadGlbOptions {
    yUp?: boolean;
    lineWidth?: number;
    memberWidth?: number;
    pointSize?: number;
    lineColor?: [number, number, number, number];
    doubleSided?: boolean;
    /** Mark single-sided surfaces as closed solids, so setSectionCaps fills their cut. */
    sectionCaps?: boolean;
    /** Above 0: also draw the viewer's feature edges (boundary + folds over this angle, degrees). */
    featureEdgeAngleDeg?: number;
    /** Return each added geometry's world-space positions. */
    keepPositions?: boolean;
}

/** One geometry loadGlb added, with its instance. */
export interface LoadedGlbGeometry {
    geometry: number;
    instance: number;
    topology: number;
    vertexCount: number;
    vertexColors: boolean;
    featureEdges: boolean;
    /** The object (an index into `LoadedGlb.objects`) each of its segments draws; -1 for none. */
    segmentObjects: Int32Array;
    positions?: Float32Array;
    /** Its index buffer (into `positions`), with keepPositions. */
    indices?: Uint32Array;
}

export interface LoadedGlb {
    /** min xyz, max xyz of what was loaded. */
    bounds: [number, number, number, number, number, number];
    featureEdges: number;
    geometries: LoadedGlbGeometry[];
    /** The model's objects, parents before children. */
    objects: LoadedGlbObjects;
    /** True when the objects are the GLB's nodes (ids are node indices), not adapy's object ids. */
    nodeObjects: boolean;
    /** The scene's extras and the root `ADA_EXT_data` extension, as the JSON text the GLB holds ("" if absent). */
    extras: string;
    adaExtData: string;
}

/** The objects of a loaded GLB as parallel tables, `count` long. */
export interface LoadedGlbObjects {
    count: number;
    ids: string[];
    names: string[];
    /** Index of each object's parent, -1 for a root. */
    parents: Int32Array;
    /** 6 per object: min xyz, max xyz in world space, of the object and everything below it. Inverted
     *  (min > max) when nothing of it is drawn. */
    bounds: Float32Array;
    triangles: Uint32Array;
    /** Object i draws the (instance, segment) pairs `segments[2k], segments[2k + 1]` for k in
     *  `segmentOffsets[i] .. segmentOffsets[i + 1]`. */
    segmentOffsets: Uint32Array;
    segments: Uint32Array;
}

/** The renderer's own camera, as `cameraPose()` reports it. Angles in degrees. */
export interface CameraPoseInfo {
    eye: [number, number, number];
    target: [number, number, number];
    up: [number, number, number];
    fov: number;
    orthographic: boolean;
    orthoHeight: number;
    azimuth: number;
    elevation: number;
    roll: number;
    distance: number;
}

export interface WebRendererApi {
    resize(width: number, height: number): void;
    render(): void;
    /** A GLB through adacpp's own reader (the one the offscreen posters use); its bytes at `bytes`. */
    loadGlb(bytes: number, length: number, opts: LoadGlbOptions): LoadedGlb;
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
        /** Lines: distance along the line per vertex (heap pointer, 0 = none), for dashes. */
        distances: number,
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
        /** Lines: width, points: diameter, in device pixels; above 1 they draw as quads. */
        lineWidth: number,
        /** Dashed lines: dash and gap lengths in the geometry's distance units; dash 0 = solid. */
        dash: number,
        gap: number,
    ): void;
    setSegmentStates(instance: number, packed: number, count: number): void;
    removeInstance(instance: number): void;
    clear(): void;
    setCamera(view: number, proj: number, orthographic: boolean): void;
    // The renderer's own camera. With setCameraControlled(true), render() and pick() draw from it and
    // setCamera is ignored. Pixels are drawing-buffer pixels (x right, y down); angles are degrees;
    // `seconds` > 0 moves there over that time, driven by cameraUpdate.
    setCameraControlled(on: boolean): void;
    /** Half a turn per viewport height. */
    cameraOrbit(dx: number, dy: number): void;
    /** What is at the target's depth follows the pointer. */
    cameraPan(dx: number, dy: number): void;
    /** Multiplies the distance: below 1 moves in. */
    cameraZoom(factor: number): void;
    /** As cameraZoom, keeping what is under the pixel (at the target's depth) under it. */
    cameraZoomAt(factor: number, x: number, y: number): void;
    cameraRoll(angleDeg: number): void;
    /** Frame a box, keeping the viewing direction. `margin` 0 is the default (1.15). */
    cameraFit(x0: number, y0: number, z0: number, x1: number, y1: number, z1: number, margin: number, seconds: number): void;
    /** front, back, left, right, top, bottom or iso; false for a name it does not know. */
    cameraView(name: string, seconds: number): boolean;
    cameraSetOrientation(azimuthDeg: number, elevationDeg: number, rollDeg: number, seconds: number): void;
    cameraPose(): CameraPoseInfo;
    /** The camera's view and projection (column-major, clip z 0..1) for the canvas' aspect. */
    cameraMatrices(): {view: Float32Array; proj: Float32Array};
    cameraSetOrthographic(on: boolean): void;
    cameraSetUpAxis(axis: "z" | "y"): void;
    /** The share of a drag's speed kept per 1/60 s after it is released; 0 stops dead. */
    cameraSetDamping(inertia: number): void;
    /** Advance transitions and glides by `dtSeconds`; true while the camera is still moving. */
    cameraUpdate(dtSeconds: number): boolean;
    cameraMoving(): boolean;
    setBackground(r: number, g: number, b: number): void;
    setLighting(ambient: number, diffuse: number): void;
    setCulling(frustum: boolean, cone: boolean, occlusion: boolean): void;
    /** `count` section planes as (nx, ny, nz, constant) floats at `planes` (heap pointer); at most 6. */
    setClipPlanes(planes: number, count: number): void;
    /** Fill the cut of every `Capped` instance (closed, single-sided solids) on each section plane. */
    setSectionCaps(enabled: boolean): void;
    /** One colour for every cap (linear rgb); `a` 0 goes back to each instance's own colour. */
    setSectionCapColor(r: number, g: number, b: number, a: number): void;
    /** A texture from RGBA8 pixels at `rgba` (heap pointer): top row first, sRGB, straight alpha, as
     *  `getImageData().data`. */
    addTexture(rgba: number, width: number, height: number): number;
    updateTexture(texture: number, rgba: number, width: number, height: number): void;
    removeTexture(texture: number): void;
    addSprite(): number;
    /** A camera-facing quad as three.js' Sprite: world anchor, `sprite.scale`, `sprite.center`, rotation
     *  (radians), tint (linear rgb, a = opacity), SpriteFlag bits, and three's renderOrder. `texture`
     *  NO_TEXTURE draws the tint alone. */
    setSprite(
        sprite: number, texture: number, x: number, y: number, z: number, scaleX: number, scaleY: number,
        centerX: number, centerY: number, rotation: number, r: number, g: number, b: number, a: number,
        flags: number, renderOrder: number,
    ): void;
    removeSprite(sprite: number): void;
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
    /** Triangle index in the geometry's original index list (index position / 3); for a point, its
     *  position in the index list (its vertex when the geometry has no index). */
    triangle: number;
    /** What was hit: Topology.Triangles or Topology.Points. Absent from renderers that only pick triangles. */
    topology?: number;
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
    readonly limits: {maxStorageBufferBindingSize: number; maxBufferSize: number} & Record<string, number>;
    readonly info?: GpuAdapterInfoLike;
    requestDevice(desc: {label?: string; requiredLimits?: Record<string, number>}): Promise<GpuDeviceLike>;
}
export interface GpuLike {
    requestAdapter(opts?: {
        powerPreference?: "low-power" | "high-performance";
        featureLevel?: "core" | "compatibility";
    }): Promise<GpuAdapterLike | null>;
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
    Clipped: 1 << 11, // cut by the section planes (setClipPlanes)
    DepthOffset: 1 << 12, // in front of coplanar faces (a negative polygonOffset)
    DepthBack: 1 << 14, // behind coplanar lines, so edges drawn on it win (a positive polygonOffset)
    Underlay: 1 << 15, // lines drawn before the faces, so a coplanar face hides them (a ground grid)
    Capped: 1 << 16, // a closed solid whose cut is filled on each section plane (setSectionCaps)
    OnTop: 1 << 17, // drawn over the model, no depth test (three.js' depthTest: false, e.g. gizmo handles)
    CullBack: 1 << 18, // transparent, yet back faces culled (a single-sided transparent material)
} as const;
export const SpriteFlag = {
    SizeAttenuation: 1 << 0, // three.js' sizeAttenuation; clear = constant size on screen (perspective)
    DepthTest: 1 << 1, // hidden where the model is in front
    Hidden: 1 << 2,
    Clipped: 1 << 3, // cut by the section planes
} as const;
/** setSprite's `texture` for a sprite without one. */
export const NO_TEXTURE = 0xffffffff;
export const SegmentFlag = {
    Hidden: 1 << 0,
    // The colour override is a matte surface (metalness 0, roughness 1), as the selection material is.
    Matte: 1 << 1,
} as const;
