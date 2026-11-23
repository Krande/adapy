// `ctx.effects` (plugin API 1.40.0): visual effects a plugin shows at runtime, kept per plugin and source id
// and handed to whichever viewport is on screen, on the playback clock.
import assert from "node:assert/strict";
import {beforeEach, test} from "node:test";

const {makePluginEffects, resetPluginEffects} = await import("@/plugins/pluginEffects");
const {PLUGIN_API_VERSION} = await import("@/plugins/registry");
const {setActiveViewport} = await import("@/viewport/api/viewportApi");
const {playbackClock} = await import("@/state/playbackClock");
type ViewportApi = import("@/viewport/api/viewportApi").ViewportApi;
type EffectsDescription = import("@/viewport/api/viewportEffects").EffectsDescription;
type ApplyEffectsOptions = import("@/viewport/api/viewportEffects").ApplyEffectsOptions;

interface Held {
    spec: EffectsDescription;
    options: ApplyEffectsOptions;
    updates: number;
    removed: boolean;
}

/** A viewport that records the effects it holds; `draws` false is one without effects (the three.js one). */
function fakeViewport(draws = true) {
    const held: Held[] = [];
    const viewport = {
        kind: draws ? "native" : "three",
        drawsEffects: () => draws,
        ...(draws
            ? {
                  applyEffects: (spec: EffectsDescription, options: ApplyEffectsOptions = {}) => {
                      const h: Held = {spec, options, updates: 0, removed: false};
                      held.push(h);
                      return {
                          update: (next: EffectsDescription) => {
                              h.spec = next;
                              h.updates++;
                          },
                          remove: () => void (h.removed = true),
                          unresolved: () => [],
                      };
                  },
              }
            : {}),
    } as unknown as ViewportApi;
    return {viewport, held, live: () => held.filter((h) => !h.removed)};
}

const SPEC: EffectsDescription = {version: 1, lights: [{position: [0, 0, 1], intensity: 2, start: 1, end: 3}]};

let withdraw: (() => void)[] = [];
beforeEach(() => {
    resetPluginEffects();
    for (const off of withdraw) off();
    withdraw = [];
});

test("the plugin API has ctx.effects: 1.40.0 or later", () => {
    const [major, minor] = PLUGIN_API_VERSION.split(".").map(Number);
    assert.ok(major > 1 || (major === 1 && minor >= 40), PLUGIN_API_VERSION);
});

test("effects are handed to the viewport on screen with their model and the playback clock, and replaced in place", () => {
    const native = fakeViewport();
    withdraw.push(setActiveViewport(native.viewport));
    const fx = makePluginEffects("weld");
    fx.apply("run-1", SPEC, {model: "run-1.glb"});
    assert.equal(native.live().length, 1);
    const [h] = native.live();
    assert.equal(h.options.model, "run-1.glb");
    // The playback clock's time.
    assert.equal(h.options.clock!.time(), playbackClock.time());
    // Again under the same id and model: the same held effects, updated; no second set.
    const next = {...SPEC, lights: []};
    fx.apply("run-1", next, {model: "run-1.glb"});
    assert.deepEqual([native.live().length, h.updates, h.spec], [1, 1, next]);
    // Another model: put on again.
    fx.apply("run-1", SPEC, {model: "other.glb"});
    assert.deepEqual([h.removed, native.live().length, native.live()[0].options.model], [true, 1, "other.glb"]);
    fx.clear("run-1");
    assert.equal(native.live().length, 0);
});

test("ids are each plugin's own", () => {
    const native = fakeViewport();
    withdraw.push(setActiveViewport(native.viewport));
    const a = makePluginEffects("a");
    const b = makePluginEffects("b");
    a.apply("x", SPEC);
    b.apply("x", SPEC);
    assert.equal(native.live().length, 2);
    a.clear("x");
    assert.equal(native.live().length, 1);
});

test("a viewport that comes on screen is handed what was applied; one that draws none says so", () => {
    const fx = makePluginEffects("weld");
    const three = fakeViewport(false);
    withdraw.push(setActiveViewport(three.viewport));
    const heard: boolean[] = [];
    fx.subscribe((drawn) => heard.push(drawn));
    fx.apply("run-1", SPEC, {model: "m"});
    assert.equal(fx.drawn(), false);

    const native = fakeViewport();
    withdraw.push(setActiveViewport(native.viewport));
    assert.deepEqual([fx.drawn(), heard], [true, [true]]);
    assert.equal(native.live().length, 1, "applied on arrival");

    // Back to the three.js viewport: taken off the one that left.
    withdraw.push(setActiveViewport(three.viewport));
    assert.deepEqual([native.live().length, heard], [0, [true, false]]);
    // No viewport at all: nothing is drawn, and nothing breaks.
    for (const off of withdraw.splice(0)) off();
    assert.equal(fx.drawn(), false);
    fx.clear("run-1");
});

test("something that is not a description is refused with a word, and changes nothing", () => {
    const native = fakeViewport();
    withdraw.push(setActiveViewport(native.viewport));
    const said: string[] = [];
    const fx = makePluginEffects("weld", (m) => said.push(m));
    fx.apply("x", null as unknown as EffectsDescription);
    assert.deepEqual([native.held.length, said.length], [0, 1]);
});
