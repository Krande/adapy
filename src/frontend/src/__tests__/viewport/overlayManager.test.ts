// The overlay manager (viewport/api/overlayManager.ts): one registry of what is drawn over the scene,
// whose lifetimes a clear, a model leaving, a renderer switch and an owner going away apply alike.
import assert from "node:assert/strict";
import {afterEach, test} from "node:test";

import type {ObjectMarks} from "@/viewport/api/objectColors";
import type {ObjectFade} from "@/viewport/api/objectFade";
import {clear, list, register, removeOwner, removeWhere, sourcesChanged, type OverlayHandle} from "@/viewport/api/overlayManager";
import {setActiveViewport, type ViewportApi} from "@/viewport/api/viewportApi";

/** A viewport that counts its live layers and what is drawn in them, and keeps its marks and fade. */
function fakeViewport(name = "v") {
    const state = {layers: new Map<string, number>(), drawn: 0, disposed: [] as string[], marks: null as string[] | null, fade: null as ObjectFade | null, fadeCalls: 0};
    const layer = (kind: string, layerName: string) => {
        const key = `${kind}:${layerName}`;
        state.layers.set(key, (state.layers.get(key) ?? 0) + 1);
        return {
            addLines: () => ++state.drawn,
            addPoints: () => ++state.drawn,
            add: () => ++state.drawn,
            remove: () => void state.drawn--,
            clear: () => {},
            unavailable: () => null,
            dispose: () => {
                state.disposed.push(key);
                state.layers.delete(key);
            },
        };
    };
    const made = new Map<string, ReturnType<typeof layer>>();
    const once = (kind: string, layerName: string) => {
        const key = `${kind}:${layerName}`;
        if (!state.layers.has(key)) made.set(key, layer(kind, layerName));
        return made.get(key)!;
    };
    const api = {
        kind: "native",
        name,
        overlays: (n: string) => once("lines", n),
        billboards: (n: string) => once("labels", n),
        images: (n: string) => once("images", n),
        setObjectMarks: (marks: ObjectMarks) => {
            const out: string[] = [];
            for (const [model, ids] of marks) for (const [id, color] of ids) out.push(`${model}/${id}=${color}`);
            state.marks = out.sort();
        },
        setObjectFade: (fade: ObjectFade | null) => {
            state.fadeCalls++;
            state.fade = fade;
        },
    } as unknown as ViewportApi;
    return {api, state};
}

const handles: OverlayHandle[] = [];
let withdraw: (() => void) | null = null;

afterEach(() => {
    for (const h of handles.splice(0)) h.remove();
    withdraw?.();
    withdraw = null;
});

/** An entry that draws one line in a layer called `layer`, counting its draws. */
function lineEntry(owner: string, lifetime: Parameters<typeof register>[0]["lifetime"], extra: Partial<Parameters<typeof register>[0]> = {}) {
    const counts = {draws: 0, cleanups: 0, removed: 0};
    const h = register({
        owner,
        lifetime,
        draw: (handle) => {
            counts.draws++;
            const layer = handle.lines(`${owner}:layer`)!;
            const id = layer.addLines({segments: new Float32Array(6)});
            return () => {
                counts.cleanups++;
                layer.remove(id);
            };
        },
        onRemoved: () => void counts.removed++,
        ...extra,
    });
    handles.push(h);
    return {h, counts};
}

test("an entry is drawn on the viewport on screen, and again on the one that takes its place", () => {
    const a = fakeViewport("a");
    withdraw = setActiveViewport(a.api);
    const {h, counts} = lineEntry("p", {kind: "scene"});
    assert.equal(a.state.drawn, 1);
    assert.equal(h.viewport, a.api);

    const b = fakeViewport("b");
    const withdrawB = setActiveViewport(b.api);
    assert.equal(a.state.drawn, 0, "taken off the viewport that left");
    assert.deepEqual(a.state.disposed, ["lines:p:layer"], "and its layer released there");
    assert.equal(b.state.drawn, 1, "drawn on the one that came");
    assert.equal(counts.draws, 2);
    assert.equal(h.viewport, b.api);

    // No viewport: nothing drawn, and drawn again when one comes.
    withdrawB();
    assert.equal(b.state.drawn, 0);
    assert.equal(h.viewport, null);
    withdraw = setActiveViewport(a.api);
    assert.equal(a.state.drawn, 1);
    h.remove();
    assert.equal(a.state.drawn, 0);
    assert.equal(counts.removed, 1);
});

test("a removed entry's handle does nothing, twice removed is once", () => {
    const a = fakeViewport();
    withdraw = setActiveViewport(a.api);
    const {h, counts} = lineEntry("p", {kind: "scene"});
    h.remove();
    h.remove();
    assert.equal(counts.removed, 1);
    assert.equal(h.alive, false);
    assert.equal(h.lines("x"), null);
    h.redraw();
    h.setVisible(false);
    h.setMarks([{model: "m", id: "1"}], "#ff0000");
    assert.equal(counts.draws, 1);
    assert.deepEqual(a.state.marks, null, "a removed entry marks nothing");
});

test("entries share a layer of one name, and the last one out disposes it", () => {
    const a = fakeViewport();
    withdraw = setActiveViewport(a.api);
    const one = lineEntry("p", {kind: "scene"});
    const two = lineEntry("p", {kind: "scene"});
    assert.equal(a.state.drawn, 2);
    one.h.remove();
    assert.deepEqual(a.state.disposed, []);
    two.h.remove();
    assert.deepEqual(a.state.disposed, ["lines:p:layer"]);
});

test("a clear removes scene, model and FEA-result entries, forgets views, and leaves tools", () => {
    const a = fakeViewport();
    withdraw = setActiveViewport(a.api);
    const told: string[] = [];
    const scene = lineEntry("p", {kind: "scene"});
    const model = lineEntry("core:m", {kind: "model", sources: ["a.glb"]});
    const result = lineEntry("p", {kind: "fea-result", token: {}});
    const tool = lineEntry("p", {kind: "until-removed"});
    const view = lineEntry("core:v", {kind: "view"}, {forget: () => void told.push("forget")});
    const plainView = lineEntry("core:w", {kind: "view"});

    clear({replacing: false});
    assert.deepEqual([scene.h.alive, model.h.alive, result.h.alive, tool.h.alive, view.h.alive, plainView.h.alive], [false, false, false, true, true, true]);
    assert.deepEqual(told, ["forget"], "the view's store is told to forget");
    assert.equal(plainView.counts.draws, 2, "a view without a store is drawn again");
    assert.deepEqual(list().map((i) => i.owner).sort(), ["core:v", "core:w", "p"]);
});

test("a replace applies each entry's policy: keep (the default), remove, forget", () => {
    const a = fakeViewport();
    withdraw = setActiveViewport(a.api);
    const told: string[] = [];
    const kept = lineEntry("p", {kind: "scene"});
    const removed = lineEntry("p", {kind: "scene"}, {onReplace: "remove"});
    const viewKept = lineEntry("core:v", {kind: "view"}, {forget: () => void told.push("kept")});
    const viewForgot = lineEntry("core:w", {kind: "view"}, {onReplace: "forget", forget: () => void told.push("forgot")});
    clear({replacing: true});
    assert.deepEqual([kept.h.alive, removed.h.alive, viewKept.h.alive, viewForgot.h.alive], [true, false, true, true]);
    assert.deepEqual(told, ["forgot"]);
});

test("a model entry goes when one of its models leaves the scene, and not before", () => {
    const a = fakeViewport();
    withdraw = setActiveViewport(a.api);
    const told: string[] = [];
    const over = lineEntry("core:m", {kind: "model", sources: ["a.glb", "b.glb"]}, {forget: () => void told.push("forget")});
    const other = lineEntry("core:n", {kind: "model", sources: ["c.glb"]});
    sourcesChanged(new Set(["a.glb", "b.glb", "c.glb"]));
    assert.ok(over.h.alive && other.h.alive);
    sourcesChanged(new Set(["a.glb", "c.glb"]));
    assert.equal(over.h.alive, false);
    assert.deepEqual(told, ["forget"]);
    assert.equal(other.h.alive, true);
    other.h.setSources(["d.glb"]);
    sourcesChanged(new Set(["a.glb", "c.glb"]));
    assert.equal(other.h.alive, false, "retargeted to a model that is not loaded");
});

test("an FEA result's entry goes with its result, and with a renderer switch", () => {
    const a = fakeViewport("a");
    withdraw = setActiveViewport(a.api);
    const token = {};
    const pinned = lineEntry("p", {kind: "fea-result", token});
    const other = lineEntry("p", {kind: "fea-result", token: {}});
    removeWhere((i) => i.lifetime.kind === "fea-result" && i.lifetime.token === token);
    assert.equal(pinned.h.alive, false);
    assert.equal(other.h.alive, true);
    const b = fakeViewport("b");
    withdraw();
    withdraw = setActiveViewport(b.api);
    assert.equal(other.h.alive, false, "the result is drawn anew on the other viewport: what was pinned to it goes");
    assert.equal(b.state.drawn, 0);
});

test("removeOwner takes an owner's entries, or those of one kind", () => {
    const a = fakeViewport();
    withdraw = setActiveViewport(a.api);
    const drawing = lineEntry("p", {kind: "scene"}, {kind: "drawing"});
    const highlight = lineEntry("p", {kind: "scene"}, {kind: "highlight"});
    const others = lineEntry("q", {kind: "scene"}, {kind: "drawing"});
    removeOwner("p", "highlight");
    assert.deepEqual([drawing.h.alive, highlight.h.alive, others.h.alive], [true, false, true]);
    removeOwner("p");
    assert.deepEqual([drawing.h.alive, others.h.alive], [false, true]);
});

test("hidden, an entry leaves the viewport and comes back when shown", () => {
    const a = fakeViewport();
    withdraw = setActiveViewport(a.api);
    const {h, counts} = lineEntry("p", {kind: "scene"});
    h.setMarks([{model: "m", id: "1"}], "#ff0000");
    h.setVisible(false);
    assert.equal(a.state.drawn, 0);
    assert.deepEqual(a.state.marks, [], "its marks withdrawn");
    assert.equal(h.lines("x"), null);
    h.setVisible(true);
    assert.equal(a.state.drawn, 1);
    assert.deepEqual(a.state.marks, ["m/1=#ff0000"]);
    assert.equal(counts.draws, 2);
});

test("every entry's marks are merged, the newer entry's colour on an object both mark, and follow the viewport", () => {
    const a = fakeViewport("a");
    withdraw = setActiveViewport(a.api);
    const older = register({owner: "p", lifetime: {kind: "scene"}});
    const newer = register({owner: "q", lifetime: {kind: "scene"}});
    handles.push(older, newer);
    older.setMarks([{model: "m", id: "1"}, {model: "m", id: "2"}], "#111111");
    newer.setMarks([{model: "m", id: "2"}], "#222222");
    assert.deepEqual(a.state.marks, ["m/1=#111111", "m/2=#222222"]);
    // Set again, an entry keeps its place: the newer one still wins.
    older.setMarks([{model: "m", id: "2"}], "#111111");
    assert.deepEqual(a.state.marks, ["m/2=#222222"]);
    const b = fakeViewport("b");
    withdraw();
    withdraw = setActiveViewport(b.api);
    assert.deepEqual(b.state.marks, ["m/2=#222222"], "the viewport that came marks them too");
    newer.remove();
    assert.deepEqual(b.state.marks, ["m/2=#111111"]);
});

test("the fade has one owner: the last to fade has it, and it follows the viewport", () => {
    const a = fakeViewport("a");
    withdraw = setActiveViewport(a.api);
    const one = register({owner: "core:clash", lifetime: {kind: "view"}});
    const two = register({owner: "core:other", lifetime: {kind: "view"}});
    handles.push(one, two);
    const fade = (opacity: number): ObjectFade => ({keep: new Map(), opacity});
    one.setFade(fade(0.2));
    assert.equal(a.state.fade?.opacity, 0.2);
    two.setFade(fade(0.5));
    assert.equal(a.state.fade?.opacity, 0.5);
    one.setFade(null);
    assert.equal(a.state.fade?.opacity, 0.5, "letting go of a fade one does not hold changes nothing");
    const b = fakeViewport("b");
    withdraw();
    assert.equal(a.state.fade, null, "taken off the viewport that left");
    withdraw = setActiveViewport(b.api);
    assert.equal(b.state.fade?.opacity, 0.5, "put on the one that came");
    two.remove();
    assert.equal(b.state.fade, null, "an owner removed lets go of it");
});
