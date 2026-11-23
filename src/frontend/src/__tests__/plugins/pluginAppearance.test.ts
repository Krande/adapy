// `ctx.appearance` (plugin API 1.38.0): appearance presets over the user's Appearance settings, which stay
// the one source of truth. "realistic" writes the HDR look and remembers what it replaced; "default" puts
// that back, not the factory defaults.
import assert from "node:assert/strict";
import {beforeEach, test} from "node:test";

const memory = new Map<string, string>();
const storage = {
    getItem: (k: string) => memory.get(k) ?? null,
    setItem: (k: string, v: string) => void memory.set(k, String(v)),
    removeItem: (k: string) => void memory.delete(k),
};
(globalThis as unknown as Record<string, unknown>).localStorage = storage;

const {APPEARANCE_DEFAULTS, REALISTIC_LOOK, appearanceIsDefault, sanitizeAppearance, useAppearanceStore} = await import("@/state/appearanceStore");
const {makePluginAppearance} = await import("@/plugins/pluginAppearance");
const {PLUGIN_API_VERSION} = await import("@/plugins/registry");

const store = () => useAppearanceStore.getState();

beforeEach(() => {
    store().reset();
    memory.clear();
});

test("the plugin API has ctx.appearance from 1.38.0", () => {
    const [major, minor] = PLUGIN_API_VERSION.split(".").map(Number);
    assert.ok(major > 1 || (major === 1 && minor >= 38), PLUGIN_API_VERSION);
});

test("realistic writes the look; default puts back the user's own settings, not the defaults", () => {
    const appearance = makePluginAppearance();
    store().setBrightness(1.2);
    store().setToneMapping("aces");
    store().setBackground("#102030");
    assert.equal(appearance.current().preset, "default");

    appearance.applyPreset("realistic");
    const on = appearance.current();
    assert.equal(on.preset, "realistic");
    assert.equal(on.toneMapping, "neutral");
    assert.equal(on.bloom, true);
    assert.equal(on.environment, true);
    assert.equal(on.brightness, REALISTIC_LOOK.brightness);
    assert.equal(store().background, "#102030", "a preset writes only the look settings");

    // Applied twice: still what the user had before the first time.
    appearance.applyPreset("realistic");
    appearance.applyPreset("default");
    assert.equal(store().brightness, 1.2);
    assert.equal(store().toneMapping, "aces");
    assert.equal(store().bloom, false);
    assert.equal(store().environment, false);
    assert.equal(store().presetRestore, null);
    assert.equal(appearance.current().preset, "default");
});

test("default with no preset applied leaves the user's look alone", () => {
    const appearance = makePluginAppearance();
    store().setExposure(2);
    store().setBloom(true);
    appearance.applyPreset("default");
    assert.equal(store().exposure, 2);
    assert.equal(store().bloom, true);
    // The realistic look set by hand, with nothing to put back: default is the defaults' look.
    for (const [k, v] of Object.entries(REALISTIC_LOOK)) useAppearanceStore.setState({[k]: v});
    appearance.applyPreset("default");
    assert.equal(store().toneMapping, APPEARANCE_DEFAULTS.toneMapping);
    assert.equal(store().environment, APPEARANCE_DEFAULTS.environment);
});

test("the settings persist with what to put back, and a reset forgets it", () => {
    const appearance = makePluginAppearance();
    store().setBrightness(0.8);
    appearance.applyPreset("realistic");
    const stored = JSON.parse(memory.get("ada-appearance") ?? "{}").state;
    assert.equal(stored.toneMapping, "neutral");
    assert.equal(stored.presetRestore.brightness, 0.8);
    // A stored value that is not a look is no restore point.
    assert.equal(sanitizeAppearance({presetRestore: "junk"}).presetRestore, null);
    assert.equal(sanitizeAppearance({presetRestore: {brightness: 99}}).presetRestore?.brightness, 2);
    store().reset();
    assert.ok(appearanceIsDefault());
});

test("subscribers hear look changes from anyone, and only those", () => {
    const appearance = makePluginAppearance();
    const heard: string[] = [];
    const stop = appearance.subscribe((now) => heard.push(now.preset));
    appearance.applyPreset("realistic");
    store().setBackground("#000000"); // not a look setting: nothing to hear
    store().setBloom(false); // the Options panel, say: no longer the preset
    stop();
    store().setBloom(true);
    assert.deepEqual(heard, ["realistic", "default"]);
});

test("an unknown preset changes nothing and is logged", () => {
    const lines: string[] = [];
    const appearance = makePluginAppearance((m) => lines.push(m));
    appearance.applyPreset("cinematic" as never);
    assert.equal(store().toneMapping, APPEARANCE_DEFAULTS.toneMapping);
    assert.equal(lines.length, 1);
    assert.deepEqual([...appearance.presets], ["realistic", "default"]);
});
