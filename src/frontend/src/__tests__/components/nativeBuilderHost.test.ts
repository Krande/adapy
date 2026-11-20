// The cell builder on the three.js-free viewport (components/viewer/cellbuilder/nativeViewHost.ts): the
// same builder scene, drawn through the viewport's overlays, picked with a camera kept in step with the
// viewport's, holding the viewport's camera still for the drags it makes, and placing its cells by the
// viewport's model offset.

import assert from "node:assert/strict";
import {test} from "node:test";
import type * as T from "three";

await import("../fixtures/panelRealm");
const g = globalThis as Record<string, unknown>;
g.requestAnimationFrame ??= (cb: () => void) => setTimeout(cb, 0) as unknown as number;
g.cancelAnimationFrame ??= (id: number) => clearTimeout(id);
// Canvases the builder paints its markers on: a 2D context that draws nothing.
const paint = () => new Proxy({} as Record<string | symbol, unknown>, {get: (t, k) => (k in t ? t[k] : () => ({width: 0}))});
(g.document as {createElement: unknown}).createElement = () => ({style: {}, width: 0, height: 0, getContext: paint});

const THREE = await import("three");
const {useCellBuilderStore} = await import("@/state/cellBuilderStore");
const {useModelState} = await import("@/state/modelState");
const {initCellBuilderScene} = await import("@/components/viewer/cellbuilder/initScene");
const {followViewportCamera, nativeViewHost} = await import("@/components/viewer/cellbuilder/nativeViewHost");
const {patchRaycaster, updateProjection} = await import("@/utils/scene/orthographicProjection");
const {runBeforeDraw} = await import("@/viewport/api/beforeDraw");
const {notifyModelLoaded} = await import("@/viewport/api/viewportApi");
type ViewportApi = import("@/viewport/api/viewportApi").ViewportApi;

/** A three.js camera's matrices as a viewport with clip depth 0..1 reports them. */
function zeroToOne(camera: T.Camera): {view: Float32Array; proj: Float32Array} {
    camera.updateMatrixWorld(true);
    const p = camera.projectionMatrix.elements;
    const proj = Float32Array.from(p);
    for (let c = 0; c < 4; c++) proj[c * 4 + 2] = 0.5 * p[c * 4 + 2] + 0.5 * p[c * 4 + 3];
    return {view: Float32Array.from(camera.matrixWorldInverse.elements), proj};
}

const ndc = (camera: T.Camera, p: T.Vector3) => p.clone().project(camera);

test("the camera follows the viewport's: the same points land on the same place on screen, in perspective", () => {
    const ref = new THREE.PerspectiveCamera(50, 1.6, 0.5, 400);
    ref.up.set(0, 0, 1);
    ref.position.set(8, -12, 6);
    ref.lookAt(1, 2, 0);
    const {view, proj} = zeroToOne(ref);
    const cam = new THREE.PerspectiveCamera();
    followViewportCamera(cam, view, proj, 50);
    for (const p of [new THREE.Vector3(1, 2, 0), new THREE.Vector3(-3, 4, 2), new THREE.Vector3(5, 5, -1)]) {
        const a = ndc(ref, p), b = ndc(cam, p);
        assert.ok(Math.abs(a.x - b.x) < 1e-4 && Math.abs(a.y - b.y) < 1e-4, `${p.toArray()} at ${a.x},${a.y}, got ${b.x},${b.y}`);
    }
    assert.ok(Math.abs(cam.near - 0.5) < 1e-3 && Math.abs(cam.far - 400) < 1, "near and far read back");
    const ray = new THREE.Raycaster();
    ray.setFromCamera(new THREE.Vector2(0, 0), cam);
    assert.ok(ray.ray.distanceToPoint(new THREE.Vector3(1, 2, 0)) < 1e-3, "a ray through the middle passes the target");
});

test("... and orthographically, as the three.js viewport's orthographic mode: parallel rays, one height", () => {
    const ref = new THREE.PerspectiveCamera(40, 1.25, 0.1, 1000);
    ref.up.set(0, 0, 1);
    ref.position.set(0, -20, 0);
    ref.lookAt(0, 0, 0);
    ref.updateMatrixWorld(true);
    updateProjection(ref, true, 20);
    const {view, proj} = zeroToOne(ref);
    const cam = new THREE.PerspectiveCamera();
    followViewportCamera(cam, view, proj, 40);
    for (const p of [new THREE.Vector3(2, 0, 1), new THREE.Vector3(-4, 3, -2)]) {
        const a = ndc(ref, p), b = ndc(cam, p);
        assert.ok(Math.abs(a.x - b.x) < 1e-4 && Math.abs(a.y - b.y) < 1e-4);
    }
    // The host installs the orthographic rays (`nativeViewHost`), as the three.js viewport does.
    patchRaycaster();
    const ray = new THREE.Raycaster();
    ray.setFromCamera(new THREE.Vector2(0.5, 0.5), cam);
    assert.ok(Math.abs(ray.ray.direction.y - 1) < 1e-6, "a ray off the middle still runs along the view");
});

/** A three.js-free viewport as far as the builder uses it, recording what it is asked. */
function fakeViewport(camera: T.PerspectiveCamera) {
    const listeners = new Map<string, ((e: unknown) => void)[]>();
    const input = {
        style: {} as Record<string, string>,
        addEventListener: (t: string, l: (e: unknown) => void) => listeners.set(t, [...(listeners.get(t) ?? []), l]),
        removeEventListener: (t: string, l: (e: unknown) => void) => listeners.set(t, (listeners.get(t) ?? []).filter((x) => x !== l)),
        getBoundingClientRect: () => ({left: 0, top: 0, width: 100, height: 100}),
        setPointerCapture() {},
        releasePointerCapture() {},
        hasPointerCapture: () => false,
        ownerDocument: g.document,
        clientHeight: 100,
    };
    let next = 1;
    const meshes = new Map<number, unknown>();
    const lines = new Map<number, unknown>();
    const gridHidden: boolean[] = [];
    let taker: ((e: PointerEvent) => ((move: PointerEvent) => void) | null) | null = null;
    let disposed = 0;
    const overlays = {
        addLines: (l: unknown) => (lines.set(next, l), next++),
        addPoints: () => next++,
        addMesh: (m: unknown) => (meshes.set(next, m), next++),
        setPositions() {},
        setGeometry() {},
        setLook() {},
        remove: (h: number) => void (meshes.delete(h), lines.delete(h)),
        clear() {},
        dispose: () => void disposed++,
    };
    const billboards = {add: () => next++, update() {}, remove() {}, clear() {}, onClick: () => () => {}, dispose() {}};
    const api = {
        kind: "native",
        overlays: () => overlays,
        billboards: () => billboards,
        cameraMatrices: () => ({...zeroToOne(camera), width: 100, height: 100}),
        cameraPose: () => ({eye: [0, 0, 0], target: [0, 0, 0], up: [0, 0, 1], fovDeg: camera.fov}),
        elements: () => ({view: input, input}),
        takePresses: (t: typeof taker) => {
            taker = t;
            return () => void (taker = null);
        },
        setGroundGridHidden: (hidden: boolean) => void gridHidden.push(hidden),
        sourceOrigin: (name: string) => (name === "result.glb" ? [1, 2, 3] : null),
    } as unknown as ViewportApi;
    const fire = (type: string, e: unknown) => (listeners.get(type) ?? []).forEach((l) => l(e));
    return {api, meshes, lines, gridHidden, fire, taker: () => taker, disposed: () => disposed};
}

const press = (x: number, y: number, more: object = {}) => {
    let stopped = false;
    const e = {
        type: "pointerdown", button: 0, clientX: x, clientY: y, pointerId: 7, pointerType: "mouse",
        shiftKey: false, ctrlKey: false, metaKey: false, altKey: false,
        stopPropagation: () => void (stopped = true), preventDefault() {}, get cancelBubble() { return stopped; },
        ...more,
    };
    return e as unknown as PointerEvent;
};

test("on the three.js-free viewport the builder draws its cells, takes its drags from the camera and follows the model offset", () => {
    // A camera looking down at a 2 m cell standing on the origin, from a corner (a face drag runs
    // along the face's normal, which a ray straight along it could not follow).
    const camera = new THREE.PerspectiveCamera(50, 1, 0.1, 100);
    camera.up.set(0, 0, 1);
    camera.position.set(6, -8, 5);
    camera.lookAt(0, 0, 1);
    const vp = fakeViewport(camera);
    useModelState.getState().setTranslation(new THREE.Vector3(0, 0, 0));
    useCellBuilderStore.setState({
        active: {modelId: "m", name: "demo/demo", revision: 1},
        cells: {c1: {id: "c1", name: "A", kind: "cell", origin: [-1, -1, 0], size: [2, 2, 2]}},
        selection: null,
        selectedCellIds: [],
        faceDragResize: true,
        gizmoMode: "none",
        mode: "idle",
        selectMode: "cell",
    } as never);

    const host = nativeViewHost(vp.api);
    assert.equal(host.kind, "native");
    const teardown = initCellBuilderScene(host);
    try {
        // The cell's six faces and its edges, through the viewport's overlays.
        assert.ok(vp.meshes.size >= 6, `cell faces drawn (${vp.meshes.size})`);
        assert.ok(vp.lines.size >= 1, "its edges and the builder's grid");
        assert.deepEqual(vp.gridHidden.at(-1), true, "the ground grid makes way for the builder's");

        // A press on the cell's face (face-drag resizing on) is a face drag: taken from the camera.
        const onCell = press(50, 50);
        vp.fire("pointerdown", onCell);
        const take = vp.taker();
        assert.ok(take, "the builder asks for the presses");
        assert.ok(take!(onCell), "the face drag holds the camera still");
        vp.fire("pointercancel", {...onCell, type: "pointercancel"});

        // A press on empty space is the camera's.
        const offCell = press(2, 2);
        vp.fire("pointerdown", offCell);
        assert.equal(take!(offCell), null);
        vp.fire("pointerup", {...offCell, type: "pointerup"});

        // A select drag (Shift + left) while the cells are edited is the builder's rectangle.
        assert.ok(take!(press(2, 2, {shiftKey: true})), "the rectangle selects cells, not the model's objects");

        // A model that replaced what the viewport shows: the cells follow its offset.
        notifyModelLoaded({model: "k", sourceName: "result.glb", sceneExtras: ""});
        assert.deepEqual(useModelState.getState().translation?.toArray(), [1, 2, 3]);
        runBeforeDraw();
    } finally {
        teardown();
    }
    assert.equal(vp.taker(), null, "the presses are left to the viewport again");
    assert.equal(vp.gridHidden.at(-1), false, "the ground grid is back");
    assert.equal(vp.meshes.size, 0, "nothing of the builder is left drawn");
    assert.ok(vp.disposed() >= 1);
    useCellBuilderStore.setState({active: null, cells: {}} as never);
});
