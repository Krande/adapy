// Hand-maintained type contract for the embed bundle. The vite build
// produces `dist-embed/index.js`; this file is copied next to it at
// build time by the `inlineCssAtRuntime` plugin so paradoc gets
// `vendor/ada-viewer/{index.js, index.d.ts}` together. Keeping this
// file hand-written (instead of generated via `tsc`) avoids tsc's
// `rootDir` complaint about embed/index.ts importing from ../src/...
// — the only consumer is paradoc, which only needs the surface API
// below.

export interface CameraPreset {
    name: string
    azimuth_deg: number
    elevation_deg: number
    roll_deg?: number
    target?: "bbox_center"
    distance?: "fit" | number
    fov_deg?: number
    margin?: number
}

export interface MountViewerOptions {
    modelBytes: Uint8Array
    camera: CameraPreset
    caption?: string
    // When true, mountViewer overlays adapy's selection tree, object
    // info, and group info panels on top of the canvas. Defaults to
    // false (canvas-only).
    showControls?: boolean
    onReady?: () => void
    onError?: (err: Error) => void
}

export interface MountedViewer {
    dispose: () => void
}

export declare function mountViewer(element: HTMLElement, opts: MountViewerOptions): MountedViewer

export type FeaArtefactFetcher = (filename: string) => Promise<ArrayBuffer>

export interface MountFeaArtefactViewerOptions {
    /** Pre-fetched fea.manifest.json body. */
    manifest: unknown
    /** Resolves manifest-relative filenames to bytes. */
    fetcher: FeaArtefactFetcher
    camera: CameraPreset
    caption?: string
    showControls?: boolean
    /** 0-based mode index to render (default 0). */
    modeIndex?: number
    onReady?: () => void
    onError?: (err: Error) => void
}

/** Mount a viewer driven by a pre-baked FEA artefact bundle (mesh
 *  GLB + per-field blobs + edge sidecar). The embed assembles a
 *  single consolidated GLB with one morph target + animation clip
 *  per mode and routes it through `mountViewer`. SimulationControls
 *  appear under `showControls: true`. */
export declare function mountFeaArtefactViewer(
    element: HTMLElement,
    opts: MountFeaArtefactViewerOptions,
): MountedViewer
