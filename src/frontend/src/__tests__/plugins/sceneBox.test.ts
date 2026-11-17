// `ctx.scene.placeBox` (plugin API 1.26.0): a box a plugin places in the scene, moved and turned with
// the transform gizmo's keys and handles, on whichever viewport is on screen.
import assert from "node:assert/strict";
import {afterEach, test} from "node:test";

import {BoxPlacement, placeBox, type BoxPlacementEnv, type PlaceBoxOptions, type PlacedBoxPose} from "@/plugins/sceneBox";
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
    // Another box: drawn anew, its handles in proportion.
    const before = view.drawn.length;
    box.set({box: [-2, -2, 0, 2, 2, 3]});
    assert.ok(view.drawn.length > before);
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
