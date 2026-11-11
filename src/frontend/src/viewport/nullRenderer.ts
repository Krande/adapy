// The stand-in for the WebGLRenderer when the main viewport is drawn by WebGPU alone (perfStore
// `adacppNoWebGL`). The viewer's scene code was written against a WebGLRenderer it is handed: it asks it
// for sizes and the pixel ratio, hangs its canvas in the page, and now and then renders through it. This
// object answers the first kind of call and ignores the second, and creates NO WebGL context: what still
// reads pixels back from WebGL (three.js' own pickers, used by a few tools besides click selection) finds
// nothing in this mode. three.js stays the scene graph, the loader and the controls.
//
// If WebGPU cannot start, or stops, the viewport is remounted with a real WebGLRenderer (ThreeCanvas).
import * as THREE from "three";

const NULL_RENDERER = Symbol("nullRenderer");

/** A WebGLRenderer-shaped object that draws nothing and owns no GL context. */
export function createNullRenderer(width: number, height: number, pixelRatio: number): THREE.WebGLRenderer {
    // A plain element in the canvas' place: no context is ever requested from it.
    const domElement = document.createElement("canvas");
    domElement.dataset.nullRenderer = "true";
    let w = width, h = height, ratio = pixelRatio;
    let target: THREE.WebGLRenderTarget | null = null;
    const clearColor = new THREE.Color(0x000000);
    const renderer = {
        [NULL_RENDERER]: true,
        domElement,
        localClippingEnabled: false,
        shadowMap: {enabled: false, type: THREE.PCFShadowMap, needsUpdate: false},
        info: {render: {calls: 0, triangles: 0, points: 0, lines: 0, frame: 0}, memory: {geometries: 0, textures: 0}, programs: []},
        capabilities: {maxTextureSize: 8192, isWebGL2: true},
        getSize: (out: THREE.Vector2) => out.set(w, h),
        getDrawingBufferSize: (out: THREE.Vector2) => out.set(Math.floor(w * ratio), Math.floor(h * ratio)),
        setSize: (nw: number, nh: number, updateStyle = true) => {
            w = nw;
            h = nh;
            if (updateStyle) {
                domElement.style.width = `${nw}px`;
                domElement.style.height = `${nh}px`;
            }
        },
        getPixelRatio: () => ratio,
        setPixelRatio: (r: number) => {
            ratio = r;
        },
        getClearColor: (out: THREE.Color) => out.copy(clearColor),
        setClearColor: (c: THREE.ColorRepresentation) => {
            clearColor.set(c);
        },
        getClearAlpha: () => 1,
        getRenderTarget: () => target,
        setRenderTarget: (t: THREE.WebGLRenderTarget | null) => {
            target = t;
        },
        // Leaves the buffer as it was handed in: callers read "nothing under the cursor".
        readRenderTargetPixels: () => {},
        getContext: () => null,
        render: () => {},
        clear: () => {},
        clearStencil: () => {},
        dispose: () => {},
    };
    renderer.setSize(width, height);
    // The one place this shape is passed off as a WebGLRenderer: everything it lacks is drawing.
    return renderer as unknown as THREE.WebGLRenderer;
}

export function isNullRenderer(renderer: THREE.WebGLRenderer | null | undefined): boolean {
    return !!renderer && NULL_RENDERER in renderer;
}
