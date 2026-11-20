// `ctx.scene.placeBox` (plugin API 1.26.0): a box a plugin places in the scene, moved and turned with
// the transform gizmo's keys and handles, on whichever viewport is on screen.
import assert from "node:assert/strict";
import {afterEach, test} from "node:test";

import {BoxPlacement, placeBox, type BoxPlacementEnv, type PlaceBoxOptions, type PlacedBoxPose} from "@/plugins/sceneBox";
import {CameraPlacement, placeCamera, type PlaceCameraOptions, type PlacedCameraPose} from "@/plugins/sceneCamera";
import {removeOwnerOverlays} from "@/plugins/sceneOverlays";
import {setSceneOffset} from "@/state/sceneFrameStore";
import type {OverlayLayer, OverlayLines, OverlayMesh} from "@/viewport/api/overlayLayer";
import {setActiveViewport, type ViewportApi} from "@/viewport/api/viewportApi";

interface Drawn {
    kind: "lines" | "mesh";
    spec: OverlayLines | OverlayMesh;
    positions: number[];
    moves: number;
    removed: boolean;
}

/** What a viewport was handed, as the numbers that were meant (it is handed 32-bit floats). */
const plain = (positions: Float32Array): number[] => Array.from(positions, (v) => Math.round(v * 1e5) / 1e5);

/** A view that records what it is asked to draw and says where a point is on screen. Seen from above
 *  by default, 100 px a unit, y up the screen; `tilt` leans it so a height shows as well, a unit of it
 *  `tilt` px up and to the right. */
function fakeView(tilt = 0) {
    const drawn: Drawn[] = [];
    const disposed: string[] = [];
    const overlays = (name: string): OverlayLayer => {
        const put = (kind: Drawn["kind"], spec: OverlayLines | OverlayMesh, positions: Float32Array): number =>
            drawn.push({kind, spec, positions: plain(positions), moves: 0, removed: false});
        const mine: number[] = [];
        const keep = (handle: number) => (mine.push(handle), handle);
        return {
            addLines: (spec) => keep(put("lines", spec, spec.segments)),
            addPoints: () => 0,
            addMesh: (spec) => keep(put("mesh", spec, spec.positions)),
            setPositions: (handle, positions) => {
                drawn[handle - 1].positions = plain(positions);
                drawn[handle - 1].moves++;
            },
            setGeometry: () => {},
            setLook: () => {},
            remove: (handle) => void (drawn[handle - 1].removed = true),
            clear: () => mine.forEach((handle) => (drawn[handle - 1].removed = true)),
            dispose: () => {
                mine.forEach((handle) => (drawn[handle - 1].removed = true));
                disposed.push(name);
            },
        };
    };
    const layers = new Map<string, OverlayLayer>();
    const viewport = {
        kind: "native",
        overlays: (name: string) => {
            if (!layers.has(name)) layers.set(name, overlays(name));
            return layers.get(name)!;
        },
        projectToScreen: (world: Float32Array, count: number, out: Float32Array) => {
            for (let i = 0; i < count; i++) {
                out[i * 3] = 400 + 100 * world[i * 3] + 0.6 * tilt * world[i * 3 + 2];
                out[i * 3 + 1] = 300 - 100 * world[i * 3 + 1] - 0.8 * tilt * world[i * 3 + 2];
                out[i * 3 + 2] = 0.5;
            }
            return {width: 800, height: 600};
        },
    } as unknown as ViewportApi;
    return {viewport, drawn, disposed, shown: () => drawn.filter((d) => !d.removed)};
}

interface Sent {
    taken: boolean;
}

/** The view's input surface and the page's keys: what is listened for, and events to send. */
function fakePage() {
    const listeners = new Map<string, Set<(e: never) => void>>();
    const target = (prefix: string) => ({
        addEventListener: (type: string, listener: (e: never) => void, capture: boolean) => {
            assert.equal(capture, true, "the capture phase, before the camera and the scene's own handlers");
            const key = prefix + type;
            if (!listeners.has(key)) listeners.set(key, new Set());
            listeners.get(key)!.add(listener);
        },
        removeEventListener: (type: string, listener: (e: never) => void) => void listeners.get(prefix + type)?.delete(listener),
    });
    const captured: number[] = [];
    const input = {
        ...target("input:"),
        // The view does not start at the page's corner.
        getBoundingClientRect: () => ({left: 10, top: 20}),
        setPointerCapture: (id: number) => void captured.push(id),
        releasePointerCapture: (id: number) => void captured.splice(captured.indexOf(id), 1),
        style: {cursor: ""},
    };
    const send = (key: string, event: Record<string, unknown>): Sent => {
        const sent = {taken: false};
        const full = {
            ...event,
            preventDefault: () => {},
            stopPropagation: () => void (sent.taken = true),
            stopImmediatePropagation: () => void (sent.taken = true),
        };
        for (const listener of [...(listeners.get(key) ?? [])]) (listener as (e: unknown) => void)(full);
        return sent;
    };
    /** A pointer event at a point of the VIEW (pixels from its corner). */
    const pointer = (type: string, x: number, y: number, more: Record<string, unknown> = {}): Sent =>
        send(`input:${type}`, {clientX: x + 10, clientY: y + 20, button: 0, pointerId: 1, ...more});
    const key = (name: string, more: Record<string, unknown> = {}): Sent =>
        send("keys:keydown", {key: name, target: {tagName: "BODY"}, ctrlKey: false, metaKey: false, altKey: false, shiftKey: false, ...more});
    const listening = () => [...listeners.entries()].filter(([, set]) => set.size > 0).map(([k]) => k).sort();
    return {input, keys: target("keys:"), pointer, key, captured, listening};
}

function setup(options: Partial<PlaceBoxOptions> = {}, tilt = 0) {
    const view = fakeView(tilt);
    const page = fakePage();
    let offset: readonly number[] | null = null;
    const offsetListeners = new Set<() => void>();
    const env: BoxPlacementEnv = {
        viewport: view.viewport,
        input: page.input as unknown as BoxPlacementEnv["input"],
        keys: page.keys as unknown as BoxPlacementEnv["keys"],
        offset: () => offset,
        onOffsetChange: (listener) => {
            offsetListeners.add(listener);
            return () => void offsetListeners.delete(listener);
        },
    };
    const poses: PlacedBoxPose[] = [];
    const modes: string[] = [];
    const ends: string[] = [];
    const box = new BoxPlacement(
        {
            // 2 x 1 on the floor, 2 high, placed by the middle of its floor: its handles reach 1.5.
            box: [-1, -0.5, 0, 1, 0.5, 2],
            position: [0, 0, 0],
            onChange: (pose) => void poses.push(pose),
            onMode: (mode) => void modes.push(mode),
            onEnd: (how) => void ends.push(how),
            ...options,
        },
        "plugin:test:box",
    );
    box.attach(env);
    const moveOffset = (next: readonly number[] | null) => {
        offset = next;
        offsetListeners.forEach((l) => l());
    };
    return {box, env, view, page, poses, modes, ends, moveOffset};
}

const lines = (view: ReturnType<typeof fakeView>) => view.shown().filter((d) => d.kind === "lines");

afterEach(() => {
    setSceneOffset(null);
    delete (globalThis as {document?: unknown}).document;
    delete (globalThis as {window?: unknown}).window;
});

test("the box is drawn as its edges and its faces, with the move arrows and the plane square at its origin", () => {
    const {view} = setup({position: [2, 3, 0], yawDeg: 90});
    const [faces, edges, ...handles] = view.shown();
    assert.equal(faces.kind, "mesh");
    assert.equal((faces.spec as OverlayMesh).indices.length, 36);
    assert.ok(((faces.spec as OverlayMesh).opacity ?? 1) < 0.5, "the faces are faint");
    assert.equal(edges.positions.length, 72);
    // Turned a quarter: the box's 2 m side lies along the scene's y.
    const ys = edges.positions.filter((_, i) => i % 3 === 1);
    assert.deepEqual([Math.min(...ys), Math.max(...ys)], [2, 4]);
    // Three arrows (three segments each) and the square (four).
    assert.deepEqual(handles.map((h) => h.positions.length / 6), [3, 3, 3, 4]);
    assert.deepEqual(handles[0].positions.slice(0, 6), [2, 3, 0, 3.5, 3, 0]);
    // A marker, not a part of the model: the camera does not frame it, and the handles show through.
    for (const d of view.shown()) assert.equal(d.spec.fitted, false);
    assert.ok(handles.every((h) => h.spec.onTop === true));
});

test("the box is drawn where the viewport placed the scene, and again when that changes", () => {
    const {view, moveOffset} = setup();
    assert.equal(view.shown()[0].spec.matrix ?? null, null);
    moveOffset([-100, -50, 0]);
    assert.deepEqual([...(view.shown()[0].spec.matrix as number[])].slice(12, 15), [-100, -50, 0]);
    // The coordinates handed over are still the scene's own.
    assert.deepEqual(view.shown()[2].positions.slice(0, 3), [0, 0, 0]);
});

test("frame: the camera is framed on the box in the world, once; a view that cannot is asked again by the next", () => {
    const fits: number[][] = [];
    let ready = false;
    const envWith = (offset: readonly number[] | null): BoxPlacementEnv => {
        const view = fakeView();
        const viewport = Object.assign(view.viewport, {
            fitBox: (min: readonly number[], max: readonly number[]) => (ready ? (fits.push([...min, ...max]), true) : false),
        });
        return {viewport, input: null, keys: null, offset: () => offset, onOffsetChange: () => () => {}};
    };
    const framed = new BoxPlacement({box: [-1, -0.5, 0, 1, 0.5, 2], position: [10, 20, 3], yawDeg: 90, frame: true, onChange: () => {}}, "plugin:test:box");
    // Not ready (no camera yet): nothing framed, and asked again when attached to the next view.
    framed.attach(envWith([-100, -50, 0]));
    assert.equal(fits.length, 0);
    ready = true;
    framed.attach(envWith([-100, -50, 0]));
    // Turned a quarter, so the 2 m side lies along y; placed in the world by the scene's offset.
    assert.deepEqual(fits.map((f) => f.map((v) => Math.round(v * 1e5) / 1e5)), [[-90.5, -31, 3, -89.5, -29, 5]]);
    // Once: the view is the user's after it, on this viewport and the one that replaces it.
    framed.attach(envWith(null));
    assert.equal(fits.length, 1);
    // Without `frame`, and on a view that cannot frame, the camera is left where it is.
    new BoxPlacement({box: [-1, -0.5, 0, 1, 0.5, 2], position: [0, 0, 0], onChange: () => {}}, "plugin:test:box2").attach(envWith(null));
    const view = fakeView();
    new BoxPlacement({box: [-1, -0.5, 0, 1, 0.5, 2], position: [0, 0, 0], frame: true, onChange: () => {}}, "plugin:test:box3").attach({
        viewport: view.viewport, input: null, keys: null, offset: () => null, onOffsetChange: () => () => {},
    });
    assert.equal(fits.length, 1);
    assert.ok(view.shown().length > 0, "drawn all the same");
});

test("a press on an arrow drags the box along that axis, snapped to the step, and is not the camera's", () => {
    const {box, view, page, poses} = setup();
    // The X arrow runs from (400, 300) to (550, 300) on screen.
    const press = page.pointer("pointerdown", 500, 303);
    assert.equal(press.taken, true);
    assert.deepEqual(page.captured, [1]);
    assert.equal(box.gizmo().dragging, true);
    // 123 px to the right is 1.23: on the 0.1 step.
    assert.equal(page.pointer("pointermove", 623, 250).taken, true);
    assert.deepEqual(poses.at(-1), {position: [1.2, 0, 0], yawDeg: 0});
    // The handles went with it.
    assert.deepEqual(lines(view)[1].positions.slice(0, 3), [1.2, 0, 0]);
    // A move that changes nothing tells nobody.
    const told = poses.length;
    page.pointer("pointermove", 624, 250);
    assert.equal(poses.length, told);
    assert.equal(page.pointer("pointerup", 624, 250).taken, true);
    assert.deepEqual(page.captured, []);
    assert.equal(box.gizmo().dragging, false);
    // The click that ends the press does not also select what is under it; the next one is the scene's.
    assert.equal(page.pointer("click", 624, 250).taken, true);
    assert.equal(page.pointer("click", 624, 250).taken, false);
    assert.deepEqual(box.pose(), {position: [1.2, 0, 0], yawDeg: 0});
});

test("a press beside the handles is the camera's: nothing is taken, nothing moves", () => {
    const {page, poses} = setup();
    assert.equal(page.pointer("pointerdown", 100, 100).taken, false);
    assert.equal(page.pointer("pointermove", 200, 200).taken, false);
    assert.equal(page.pointer("pointerup", 200, 200).taken, false);
    assert.equal(page.pointer("click", 200, 200).taken, false);
    assert.deepEqual(page.captured, []);
    assert.equal(poses.length, 0);
    // Another button is the camera's on a handle too.
    assert.equal(page.pointer("pointerdown", 500, 300, {button: 2}).taken, false);
});

test("the plane square drags the box in x and y at once, and never off its floor", () => {
    const {page, poses} = setup({position: [0, 0, 0.5]});
    // The square spans 0.375 to 0.75 in x and y: (437..475, 262..225) on screen.
    assert.equal(page.pointer("pointerdown", 450, 240).taken, true);
    page.pointer("pointermove", 450 + 200, 240 - 110);
    assert.deepEqual(poses.at(-1), {position: [2, 1.1, 0.5], yawDeg: 0});
    page.pointer("pointerup", 650, 130);
});

test("a move along Z needs a view that shows height, and a box kept on its floor has no such arrow", () => {
    // Leaning view: a unit of height is 30 px to the right and 40 px up the screen.
    const tilted = setup({}, 50);
    assert.equal(tilted.page.pointer("pointerdown", 418, 276).taken, true);
    tilted.page.pointer("pointermove", 418 + 60, 276 - 80);
    assert.deepEqual(tilted.poses.at(-1), {position: [0, 0, 2], yawDeg: 0});

    const flat = setup({constrain: {translate: "xy"}}, 50);
    assert.deepEqual(lines(flat.view).slice(1).map((h) => h.positions.length / 6), [3, 3, 4]);
    assert.equal(flat.page.pointer("pointerdown", 418, 276).taken, false);
});

test("R shows the turn ring, and a drag on it turns the box in steps of 15 degrees", () => {
    const {box, view, page, poses, modes} = setup({yawDeg: 10});
    assert.equal(page.key("r").taken, true);
    assert.deepEqual(modes, ["rotate"]);
    assert.equal(box.gizmo().mode, "rotate");
    // One ring, of the reach's radius, about the origin.
    assert.deepEqual(lines(view).slice(1).map((h) => h.positions.length / 6), [48]);
    // Pressed at the ring's east point (1.5, 0) and dragged to its north: a quarter turn anticlockwise.
    assert.equal(page.pointer("pointerdown", 550, 300).taken, true);
    page.pointer("pointermove", 506, 194); // 45 degrees round
    assert.deepEqual(poses.at(-1), {position: [0, 0, 0], yawDeg: 55});
    page.pointer("pointermove", 400, 150);
    assert.deepEqual(poses.at(-1), {position: [0, 0, 0], yawDeg: 100});
    // On round past half a turn: the same pose the short way.
    page.pointer("pointermove", 250, 300);
    assert.deepEqual(poses.at(-1), {position: [0, 0, 0], yawDeg: -170});
    page.pointer("pointerup", 250, 300);
    // The box turned with it, and the arrows are back with G.
    assert.equal(page.key("G").taken, true);
    assert.deepEqual(modes, ["rotate", "translate"]);
    assert.deepEqual(lines(view).slice(1).map((h) => h.positions.length / 6), [3, 3, 3, 4]);
});

test("X locks the move to that axis: the box follows the pointer with no button held until a click puts it down", () => {
    const {box, view, page, poses} = setup();
    assert.equal(page.key("x").taken, true);
    assert.deepEqual(box.gizmo(), {mode: "translate", axisLock: 0, following: true, dragging: false});
    assert.equal(page.input.style.cursor, "move");
    // The handles give way to the axis' guide line.
    assert.deepEqual(lines(view).slice(1).map((h) => h.positions.length / 6), [1]);
    // The first move is where the pointer happened to be: the box does not jump to it.
    assert.equal(page.pointer("pointermove", 700, 100).taken, false);
    assert.equal(poses.length, 0);
    page.pointer("pointermove", 650, 500);
    assert.deepEqual(poses.at(-1), {position: [-0.5, 0, 0], yawDeg: 0});
    // A click puts it down, lets the axis go, and is not also the scene's.
    assert.equal(page.pointer("pointerdown", 650, 500).taken, true);
    assert.equal(page.pointer("click", 650, 500).taken, true);
    assert.deepEqual(box.gizmo(), {mode: "translate", axisLock: null, following: false, dragging: false});
    assert.equal(page.input.style.cursor, "");
    assert.deepEqual(lines(view).slice(1).map((h) => h.positions.length / 6), [3, 3, 3, 4]);
    page.pointer("pointermove", 100, 100);
    assert.deepEqual(box.pose(), {position: [-0.5, 0, 0], yawDeg: 0});
});

test("Escape puts an axis-locked move back and keeps the placement; the same key again releases the lock", () => {
    const {box, page, poses, ends} = setup({position: [1, 1, 0]});
    page.key("y");
    page.pointer("pointermove", 400, 300);
    page.pointer("pointermove", 400, 100);
    assert.deepEqual(poses.at(-1), {position: [1, 3, 0], yawDeg: 0});
    assert.equal(page.key("Escape").taken, true);
    assert.deepEqual(poses.at(-1), {position: [1, 1, 0], yawDeg: 0});
    assert.deepEqual(box.gizmo(), {mode: "translate", axisLock: null, following: false, dragging: false});
    assert.deepEqual(ends, []);
    // Y, then Y again: locked, then free, and the box stays where the pointer left it.
    page.key("y");
    page.pointer("pointermove", 400, 300);
    page.pointer("pointermove", 400, 250);
    page.key("y");
    assert.equal(box.gizmo().following, false);
    assert.deepEqual(box.pose(), {position: [1, 1.5, 0], yawDeg: 0});
    // Enter puts a locked move down too.
    page.key("x");
    page.pointer("pointermove", 400, 300);
    page.pointer("pointermove", 500, 300);
    assert.equal(page.key("Enter").taken, true);
    assert.deepEqual(box.pose(), {position: [2, 1.5, 0], yawDeg: 0});
    assert.deepEqual(ends, []);
});

test("a key that is not this box's to answer: a form field's, a shortcut with a modifier, an axis it cannot go", () => {
    const {box, page, modes} = setup({constrain: {translate: "xy", rotate: "none"}});
    // Typed in a number field: the field's.
    assert.equal(page.key("x", {target: {tagName: "INPUT"}}).taken, false);
    assert.equal(page.key("Escape", {target: {tagName: "INPUT"}}).taken, false);
    assert.equal(page.key("r", {ctrlKey: true}).taken, false); // reload
    assert.equal(page.key("z", {metaKey: true}).taken, false); // undo
    assert.equal(page.key("X", {shiftKey: true}).taken, false);
    assert.equal(page.key("a").taken, false);
    assert.equal(box.gizmo().axisLock, null);
    // The gizmo's keys are taken even where this box cannot follow them, so they are no other shortcut.
    assert.equal(page.key("z").taken, true);
    assert.equal(box.gizmo().axisLock, null);
    assert.equal(page.key("r").taken, true);
    assert.equal(box.gizmo().mode, "translate");
    assert.deepEqual(modes, []);
});

test("in the turn gizmo only Z is an axis to lock, and it turns as before", () => {
    const {box, page} = setup();
    page.key("r");
    page.key("x");
    assert.equal(box.gizmo().axisLock, null);
    page.key("z");
    assert.deepEqual(box.gizmo(), {mode: "rotate", axisLock: 2, following: false, dragging: false});
    assert.equal(page.pointer("pointerdown", 550, 300).taken, true);
});

test("the plugin moves the box (a form's numbers): it is redrawn there and nobody is told", () => {
    const {box, view, page, poses} = setup();
    box.set({position: [3, 4, 1], yawDeg: 33.5});
    assert.deepEqual(box.pose(), {position: [3, 4, 1], yawDeg: 33.5});
    assert.deepEqual(lines(view)[1].positions.slice(0, 3), [3, 4, 1]);
    assert.equal(poses.length, 0);
    // The handles are where the box is: the X arrow is grabbed there now.
    assert.equal(page.pointer("pointerdown", 500, 300).taken, false);
    assert.equal(page.pointer("pointerdown", 400 + 350, 300 - 400).taken, true);
    // Said back mid-drag, as a form that follows the box does: the drag goes on.
    page.pointer("pointermove", 750 + 100, -100);
    box.set({position: [4, 4, 1]});
    page.pointer("pointermove", 750 + 200, -100);
    assert.deepEqual(poses.at(-1), {position: [5, 4, 1], yawDeg: 33.5});
    page.pointer("pointerup", 950, -100);
    // Another box (1.32.0): what was drawn, moved -- nothing drawn anew, so what a plugin drew over the
    // box stays over it -- its handles in proportion.
    const before = view.drawn.length;
    box.set({box: [-2, -2, 0, 2, 2, 3]});
    assert.equal(view.drawn.length, before);
    const xs = lines(view)[0].positions.filter((_, i) => i % 3 === 0);
    assert.equal(Math.max(...xs) - Math.min(...xs) > 3.9, true, "the edges are the new box's");
    assert.ok(Math.abs(lines(view)[1].positions[3] - lines(view)[1].positions[0]) > 2, "the arrows as long as the new box is large");
    assert.throws(() => box.set({box: [0, 0, 0, 1, 1]}), /six finite numbers/);
    assert.throws(() => box.set({position: [0, Number.NaN, 0]}), /three finite numbers/);
});

test("the plugin picks the gizmo too, for a page with no keyboard", () => {
    const {box, view, modes} = setup();
    box.setMode("rotate");
    assert.deepEqual(modes, ["rotate"]);
    assert.deepEqual(lines(view).slice(1).map((h) => h.positions.length / 6), [48]);
    box.setMode("rotate");
    assert.deepEqual(modes, ["rotate"]);
});

test("detached, the box takes no pointer and no key, and draws nothing; attached again it is where it was", () => {
    const {box, env, view, page} = setup();
    page.pointer("pointerdown", 500, 300);
    page.pointer("pointermove", 600, 300);
    assert.deepEqual(page.captured, [1]);
    box.detach();
    assert.deepEqual(page.listening(), []);
    assert.deepEqual(page.captured, [], "a drag in progress lets the pointer go");
    assert.equal(view.shown().length, 0);
    assert.deepEqual(view.disposed, ["plugin:test:box"]);
    assert.equal(page.key("r").taken, false);
    box.attach(env);
    assert.deepEqual(box.pose(), {position: [1, 0, 0], yawDeg: 0});
    assert.deepEqual(lines(view)[1].positions.slice(0, 3), [1, 0, 0]);
});

test("a view without an input surface still shows the box where the plugin puts it", () => {
    const view = fakeView();
    const box = new BoxPlacement({box: [0, 0, 0, 1, 1, 1], position: [0, 0, 0], onChange: () => {}}, "plugin:test:box");
    box.attach({viewport: view.viewport, input: null, keys: null, offset: () => null, onOffsetChange: () => () => {}});
    assert.equal(view.shown().length, 6);
    box.set({position: [5, 0, 0]});
    assert.deepEqual(view.shown()[1].positions.slice(0, 3), [5, 0, 0]);
});

// ----- through `placeBox`, as a plugin calls it ------------------------------------------------------

function onPage() {
    const page = fakePage();
    (globalThis as {document?: unknown}).document = {querySelector: (selector: string) => (selector === "[data-viewport-input]" ? page.input : null)};
    (globalThis as {window?: unknown}).window = page.keys;
    return page;
}

test("placeBox draws in the viewport on screen, follows the one that replaces it, and ends with Enter or Escape", () => {
    const page = onPage();
    const first = fakeView();
    const withdraw = setActiveViewport(first.viewport);
    setSceneOffset([10, 0, 0]);
    const poses: PlacedBoxPose[] = [];
    const ends: string[] = [];
    const placed = placeBox("p", {box: [-1, -0.5, 0, 1, 0.5, 2], position: [0, 0, 0], onChange: (pose) => void poses.push(pose), onEnd: (how) => void ends.push(how)});
    assert.equal(first.shown().length, 6);
    assert.deepEqual([...(first.shown()[0].spec.matrix as number[])].slice(12, 15), [10, 0, 0]);
    // The scene stands 10 to the right: so do the handles on screen.
    assert.equal(page.pointer("pointerdown", 500, 300).taken, false);
    assert.equal(page.pointer("pointerdown", 1500, 300).taken, true);
    page.pointer("pointermove", 1700, 300);
    page.pointer("pointerup", 1700, 300);
    assert.deepEqual(poses, [{position: [2, 0, 0], yawDeg: 0}]);

    // Another viewport takes the screen: the box is drawn there, where it stood.
    const second = fakeView();
    const withdrawSecond = setActiveViewport(second.viewport);
    assert.equal(first.shown().length, 0);
    assert.equal(second.shown().length, 6);
    assert.deepEqual(second.shown()[2].positions.slice(0, 3), [2, 0, 0]);

    assert.equal(page.key("Enter").taken, true);
    assert.deepEqual(ends, ["enter"]);
    assert.equal(second.shown().length, 0);
    assert.deepEqual(page.listening(), [], "the pointer and the keys are given back");
    assert.equal(page.key("g").taken, false);
    placed.end(); // twice is once
    assert.deepEqual(ends, ["enter"]);
    withdrawSecond();
    withdraw();
});

test("the plugin's own end, and removing the owner from the scene, take the box away without calling onEnd", () => {
    const page = onPage();
    const view = fakeView();
    const withdraw = setActiveViewport(view.viewport);
    const ends: string[] = [];
    const options = {box: [0, 0, 0, 1, 1, 1], position: [0, 0, 0], onChange: () => {}, onEnd: (how: string) => void ends.push(how)};
    const a = placeBox("p", options);
    a.end();
    assert.equal(view.shown().length, 0);
    assert.deepEqual(page.listening(), []);
    placeBox("p", options);
    placeBox("q", options);
    removeOwnerOverlays("p");
    assert.equal(view.shown().length, 6, "another owner's box stays");
    removeOwnerOverlays("q");
    assert.equal(view.shown().length, 0);
    assert.deepEqual(page.listening(), []);
    assert.deepEqual(ends, []);
    withdraw();
});

test("with no viewport on screen there is nothing to draw in, and the box appears when one mounts", () => {
    onPage();
    const placed = placeBox("p", {box: [0, 0, 0, 1, 1, 1], position: [1, 2, 3], onChange: () => {}});
    assert.deepEqual(placed.pose(), {position: [1, 2, 3], yawDeg: 0});
    const view = fakeView();
    const withdraw = setActiveViewport(view.viewport);
    assert.equal(view.shown().length, 6);
    withdraw();
    assert.equal(view.shown().length, 0);
    placed.end();
});

// ----- 1.32.0: the resize handles, a box that turns every way --------------------------------------

test("resize: S brings up a handle on each face, and a drag moves that face, the opposite one staying, the size snapped", () => {
    const {box, view, page, poses, modes} = setup({resize: true});
    assert.equal(page.key("s").taken, true);
    assert.deepEqual(modes, ["resize"]);
    // Six arrows (three segments each), one a face.
    assert.deepEqual(lines(view).slice(1).map((h) => h.positions.length / 6), [3, 3, 3, 3, 3, 3]);
    // The +x face's handle runs from its middle (1, 0, 1) out half a unit: (500, 300) to (550, 300) seen from above.
    assert.equal(page.pointer("pointerdown", 525, 301).taken, true);
    page.pointer("pointermove", 575, 301);
    assert.deepEqual(poses.at(-1), {position: [0, 0, 0], yawDeg: 0, box: [-1, -0.5, 0, 1.5, 0.5, 2]});
    // Pushed in past the opposite face: the box keeps the least size, the move's step.
    page.pointer("pointermove", 100, 301);
    assert.deepEqual(poses.at(-1)?.box, [-1, -0.5, 0, -0.9, 0.5, 2]);
    page.pointer("pointerup", 100, 301);
    assert.deepEqual(box.pose(), {position: [0, 0, 0], yawDeg: 0, box: [-1, -0.5, 0, -0.9, 0.5, 2]});
    // The edges went with it.
    const xs = lines(view)[0].positions.filter((_, i) => i % 3 === 0);
    assert.deepEqual([Math.min(...xs), Math.max(...xs)], [-1, -0.9]);
});

test("resize: the -y face of a turned box is dragged along its own normal", () => {
    const {page, poses} = setup({resize: {faces: ["-y", "+y"], minSize: 0.2}, yawDeg: 90, mode: "resize"});
    // Turned a quarter: the box's -y face looks along the scene's +x; its middle (0.5, 0, 1), its handle to (1, 0, 1).
    assert.equal(page.pointer("pointerdown", 460, 300).taken, true);
    page.pointer("pointermove", 460 + 120, 300);
    assert.deepEqual(poses.at(-1), {position: [0, 0, 0], yawDeg: 90, box: [-1, -1.7, 0, 1, 0.5, 2]});
});

test("resize: the faces named only, X / Y / Z keep one axis' handles, and S is not taken by a box without them", () => {
    const named = setup({resize: {faces: ["+x", "-x", "+z"]}});
    named.page.key("s");
    assert.equal(lines(named.view).length - 1, 3);
    assert.equal(named.page.key("x").taken, true);
    assert.deepEqual(named.box.gizmo(), {mode: "resize", axisLock: 0, following: false, dragging: false});
    assert.equal(lines(named.view).length - 1, 2);
    // No face of y has a handle: the key is the gizmo's, and changes nothing.
    named.page.key("y");
    assert.equal(named.box.gizmo().axisLock, 0);
    named.page.key("g");
    assert.equal(named.box.gizmo().mode, "translate");

    const plain = setup();
    assert.equal(plain.page.key("s").taken, false);
    plain.box.setMode("resize");
    assert.equal(plain.box.gizmo().mode, "translate");
    assert.equal("box" in plain.box.pose(), false, "no box in the pose of a box that cannot be resized");
});

test("rotate xyz: three rings; the Z ring turns the axes about Z, X then the X ring turns them about X", () => {
    const flat = setup({constrain: {rotate: "xyz"}});
    assert.deepEqual(flat.box.pose(), {position: [0, 0, 0], yawDeg: 0, axes: [[1, 0, 0], [0, 1, 0], [0, 0, 1]]});
    flat.page.key("r");
    assert.deepEqual(lines(flat.view).slice(1).map((h) => h.positions.length / 6), [48, 48, 48]);
    // Seen from above the X and Y rings are lines through the middle: the Z ring's east point is its own.
    assert.equal(flat.page.pointer("pointerdown", 550, 300).taken, true);
    flat.page.pointer("pointermove", 400, 150);
    assert.deepEqual(flat.poses.at(-1), {position: [0, 0, 0], yawDeg: 90, axes: [[0, 1, 0], [-1, 0, 0], [0, 0, 1]]});
    flat.page.pointer("pointerup", 400, 150);

    // Leaning view; X keeps the X ring only (its top is the Z ring's north point too).
    const lean = setup({constrain: {rotate: "xyz"}}, 50);
    lean.page.key("r");
    assert.equal(lean.page.key("x").taken, true);
    assert.deepEqual(lines(lean.view).slice(1).map((h) => h.positions.length / 6), [48]);
    // The X ring: (400 + 45 sin a, 300 - 150 cos a - 60 sin a). From a = 0 to a quarter round.
    assert.equal(lean.page.pointer("pointerdown", 400, 150).taken, true);
    lean.page.pointer("pointermove", 445, 240);
    assert.deepEqual(lean.poses.at(-1), {position: [0, 0, 0], yawDeg: 0, axes: [[1, 0, 0], [0, 0, 1], [0, -1, 0]]});
});

test("rotate xyz: the plugin turns the box by its axes; a box turned about Z only has none", () => {
    const {box, view} = setup({constrain: {rotate: "xyz"}, axes: [[0, 2, 0], [0, 0.1, 3]]});
    // y made square to x and unit, z their cross product.
    assert.deepEqual(box.pose().axes, [[0, 1, 0], [0, 0, 1], [1, 0, 0]]);
    // The box's own x (2 long) along the scene's y, its z (2 high) along the scene's x.
    const edges = lines(view)[0].positions;
    const range = (k: number) => {
        const v = edges.filter((_, i) => i % 3 === k);
        return [Math.min(...v), Math.max(...v)];
    };
    assert.deepEqual([range(0), range(1), range(2)], [[0, 2], [-1, 1], [-0.5, 0.5]]);
    box.set({yawDeg: 90});
    // A yaw given such a box stands it upright, turned by that much.
    const turned = box.pose().axes!.flat().map((v) => Math.round(v * 1e9) / 1e9 || 0);
    assert.deepEqual(turned, [0, 1, 0, -1, 0, 0, 0, 0, 1]);
    assert.equal(box.pose().yawDeg, 90);
    assert.throws(() => box.set({axes: [[0, 0, 0], [0, 1, 0]]}), /axes needs/);
    assert.equal("axes" in setup().box.pose(), false);
});
// ----- `ctx.scene.placeCamera` (1.32.0): a camera placed with the same gizmo's keys ---------------

function cameraSetup(options: Partial<PlaceCameraOptions> = {}, tilt = 0) {
    const view = fakeView(tilt);
    const page = fakePage();
    const env: BoxPlacementEnv = {
        viewport: view.viewport,
        input: page.input as unknown as BoxPlacementEnv["input"],
        keys: page.keys as unknown as BoxPlacementEnv["keys"],
        offset: () => null,
        onOffsetChange: () => () => {},
    };
    const poses: PlacedCameraPose[] = [];
    const ends: string[] = [];
    // Five back from its target along -y, looking along +y: a body half a unit long, handles 0.75.
    const camera = new CameraPlacement(
        {position: [0, -5, 0], target: [0, 0, 0], onChange: (pose) => void poses.push(pose), onEnd: (how) => void ends.push(how), ...options},
        "plugin:test:camera",
    );
    camera.attach(env);
    return {camera, view, page, poses, ends};
}
const round3 = (v: readonly number[]) => v.map((x) => Math.round(x * 1e3) / 1e3 || 0);

test("placeCamera: a body, a lens and an arrow to the target; a move takes the camera and it goes on looking at the target", () => {
    const {camera, view, page, poses} = cameraSetup();
    const [faces, body, arrow, ...handles] = view.shown();
    assert.equal(faces.kind, "mesh");
    assert.equal(body.positions.length / 6, 12 + 4 + 4 + 2);
    assert.deepEqual(arrow.positions.slice(0, 6), [0, -5, 0, 0, 0, 0]);
    assert.deepEqual(handles.map((h) => h.positions.length / 6), [3, 3, 3, 4]);
    // The X arrow: (400, 800) to (475, 800) seen from above.
    assert.equal(page.pointer("pointerdown", 450, 801).taken, true);
    page.pointer("pointermove", 550, 801);
    assert.deepEqual(poses.at(-1), {position: [1, -5, 0], target: [0, 0, 0]});
    page.pointer("pointerup", 550, 801);
    assert.deepEqual(view.shown()[2].positions.slice(0, 6), [1, -5, 0, 0, 0, 0]);
    camera.set({target: [2, 2, 2]});
    assert.deepEqual(camera.pose(), {position: [1, -5, 0], target: [2, 2, 2]});
    assert.equal(poses.length, 1, "the plugin's own set tells nobody");
});

test("placeCamera: R turns it about where it stands, the flat ring its yaw, the standing ring its pitch, the target round with it", () => {
    const flat = cameraSetup();
    assert.equal(flat.page.key("r").taken, true);
    assert.deepEqual(lines(flat.view).slice(2).map((h) => h.positions.length / 6), [48, 48]);
    // The flat ring's east point, dragged a quarter round anticlockwise: it looks along -x now.
    assert.equal(flat.page.pointer("pointerdown", 475, 800).taken, true);
    flat.page.pointer("pointermove", 400, 725);
    assert.deepEqual(flat.poses.at(-1), {position: [0, -5, 0], target: [-5, -5, 0]});
    flat.page.pointer("pointerup", 400, 725);
    // Z keeps the flat ring alone; X is no axis of the turn.
    flat.page.key("z");
    assert.deepEqual(lines(flat.view).slice(2).map((h) => h.positions.length / 6), [48]);
    flat.page.key("x");
    assert.equal(flat.camera.gizmo().axisLock, 2);

    // Leaning, so the standing ring shows: from its top (straight up of the camera) an eighth round, back and down.
    const lean = cameraSetup({mode: "rotate"}, 50);
    assert.equal(lean.page.pointer("pointerdown", 422.5, 770).taken, true);
    lean.page.pointer("pointermove", 415.9, 831.8);
    const pose = lean.poses.at(-1)!;
    assert.deepEqual(pose.position, [0, -5, 0]);
    // Tilted 45 degrees up from looking along +y, still 5 from the camera.
    assert.deepEqual(round3(pose.target), [0, -1.464, 3.536]);
});

test("placeCamera: keys as a box's -- S is not the camera's, Escape ends it -- and a bad pose is refused", () => {
    const {camera, page, ends} = cameraSetup();
    assert.equal(page.key("s").taken, false);
    assert.equal(page.key("x").taken, true);
    assert.equal(camera.gizmo().following, true);
    page.key("Escape");
    assert.equal(camera.gizmo().following, false);
    assert.deepEqual(ends, []);
    page.key("Enter");
    assert.deepEqual(ends, ["enter"]);
    assert.throws(() => new CameraPlacement({position: [0, 0], target: [0, 0, 0], onChange: () => {}}, "x"), /three finite numbers/);
    assert.throws(() => camera.set({size: 0}), /size/);
});

test("placeCamera through ctx: drawn on the viewport on screen and taken away with its owner", () => {
    onPage();
    const view = fakeView();
    const withdraw = setActiveViewport(view.viewport);
    const placed = placeCamera("p", {position: [0, -5, 0], target: [0, 0, 0], onChange: () => {}});
    assert.equal(view.shown().length, 3 + 4);
    removeOwnerOverlays("p");
    assert.equal(view.shown().length, 0);
    placed.end();
    withdraw();
});