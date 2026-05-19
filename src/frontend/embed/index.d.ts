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
