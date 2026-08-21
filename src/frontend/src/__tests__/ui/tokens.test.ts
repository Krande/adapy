import assert from "node:assert/strict";
import {test} from "node:test";

import {computeThemeVars, isDarkTheme, SEMANTIC_COLORS, THEME_VAR_NAMES} from "../../ui/themeTokens";
import {SELECTION_COLOR} from "../../ui/selectionColor";
import {THEME_PRESETS} from "../../state/themeStore";

// Runs under plain `node --test` with no jsdom: ui/themeTokens is deliberately
// DOM-free so the token maths can be tested without a browser environment.
// (Importing themeStore for its presets is safe — its module-level paint is guarded
// on `typeof document`.)

const DARK = THEME_PRESETS.dark.theme;
const LIGHT = THEME_PRESETS.mist.theme;

test("the nine pre-design-system var names are emitted unchanged", () => {
    // These are consumed by every existing panel via PANEL_CHROME, by plugin code
    // through AdaPluginContext.theme, and by ~11 files reading var(--ada-panel-*)
    // directly. Renaming or dropping one silently unstyles them.
    const v = computeThemeVars(DARK);
    assert.equal(v["--ada-panel-bg"], DARK.bg);
    assert.equal(v["--ada-panel-border"], DARK.border);
    assert.equal(v["--ada-panel-text"], DARK.text);
    assert.equal(v["--ada-panel-surface"], DARK.surface);
    assert.equal(v["--ada-panel-text-muted"], DARK.textMuted);
    assert.equal(v["--ada-accent"], SEMANTIC_COLORS.accent);
    assert.equal(v["--ada-pass"], SEMANTIC_COLORS.pass);
    assert.equal(v["--ada-warn"], SEMANTIC_COLORS.warn);
    assert.equal(v["--ada-fail"], SEMANTIC_COLORS.fail);
});

test("every documented token is present for every shipped preset", () => {
    for (const [id, preset] of Object.entries(THEME_PRESETS)) {
        const v = computeThemeVars(preset.theme);
        for (const name of THEME_VAR_NAMES) {
            assert.ok(name in v, `${id} is missing ${name}`);
            assert.ok(
                typeof v[name] === "string" && v[name].trim().length > 0,
                `${id} has an empty value for ${name}`,
            );
        }
    }
});

test("no token resolves to the literal string undefined", () => {
    // A missing template interpolation produces "color-mix(in srgb, undefined 88%, …)",
    // which the browser drops silently rather than erroring — exactly the kind of bug
    // that survives a visual review.
    for (const preset of Object.values(THEME_PRESETS)) {
        for (const [name, value] of Object.entries(computeThemeVars(preset.theme))) {
            assert.ok(!value.includes("undefined"), `${name} contains "undefined": ${value}`);
            assert.ok(!value.includes("null"), `${name} contains "null": ${value}`);
        }
    }
});

test("selection token is the same value the three.js highlight material uses", () => {
    // The whole point of ui/selectionColor: an Outliner row and the highlighted
    // geometry must be one colour by construction, not by two people picking blue.
    assert.equal(computeThemeVars(DARK)["--ada-select"], SELECTION_COLOR);
});

test("theme direction is read from the text colour, not the background", () => {
    // Panel backgrounds are rgba with alpha as low as 0.5, so a "light" panel over a
    // dark viewport composites to something dark. Text is always opaque and always
    // chosen to contrast, which makes it the honest signal.
    assert.equal(isDarkTheme(THEME_PRESETS.slate.theme), true);
    assert.equal(isDarkTheme(THEME_PRESETS.dark.theme), true);
    assert.equal(isDarkTheme(THEME_PRESETS.pale.theme), true, "pale has white text → dark direction");
    assert.equal(isDarkTheme(THEME_PRESETS.mist.theme), false, "mist has near-black text → light direction");
});

test("isDarkTheme handles 3-digit hex and unknown colour formats", () => {
    assert.equal(isDarkTheme({text: "#fff"}), true);
    assert.equal(isDarkTheme({text: "#000"}), false);
    // Non-hex (a custom swatch that isn't 6-digit hex) assumes dark, the common case,
    // rather than throwing.
    assert.equal(isDarkTheme({text: "rgb(255,255,255)"}), true);
});

test("app chrome flips with direction and is opaque", () => {
    // surface-0 sits behind the docks and the canvas; a translucent value there would
    // composite against the browser's own backdrop.
    const dark = computeThemeVars(DARK)["--ada-surface-0"];
    const light = computeThemeVars(LIGHT)["--ada-surface-0"];
    assert.notEqual(dark, light);
    for (const v of [dark, light]) {
        assert.match(v, /^#[0-9a-f]{6}$/i, `surface-0 must be an opaque hex, got ${v}`);
    }
});

test("surface aliases point at the panel theme so old and new names agree", () => {
    const v = computeThemeVars(DARK);
    assert.equal(v["--ada-surface-1"], v["--ada-panel-bg"]);
    assert.equal(v["--ada-surface-2"], v["--ada-panel-surface"]);
    assert.equal(v["--ada-text"], v["--ada-panel-text"]);
    assert.equal(v["--ada-text-muted"], v["--ada-panel-text-muted"]);
    assert.equal(v["--ada-border"], v["--ada-panel-border"]);
});

test("derived tokens are valid color-mix() expressions", () => {
    const v = computeThemeVars(DARK);
    for (const name of ["--ada-surface-3", "--ada-text-subtle", "--ada-border-strong", "--ada-focus-ring"]) {
        assert.match(v[name], /^color-mix\(in srgb, .+ \d+%, .+\)$/, `${name}: ${v[name]}`);
    }
});
