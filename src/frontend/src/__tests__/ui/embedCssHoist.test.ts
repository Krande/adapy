import assert from "node:assert/strict";
import {test} from "node:test";

// @ts-expect-error — plain .mjs build helper, no type declarations by design.
import {buildScopedEmbedCss, extractHoistableCss} from "../../../vite.plugin-embed-css.mjs";

// Guards the single most dangerous thing in the design-system change.
//
// The embed wraps its CSS in `@scope (.ada-viewer-scope)`. @scope only matches
// DESCENDANTS of the scope root, and <html> is not one — so a `:root { --token: … }`
// rule inside the wrapper matches nothing and every token silently falls back. The
// embed renders unstyled, and nothing in CI would notice, because the embed has no
// automated consumer. Hence these tests.

test("hoists a :root custom-property block", () => {
    const css = ":root{--a:1px;--b:2px}.x{color:red}";
    const out = extractHoistableCss(css);
    assert.match(out, /:root\{--a:1px;--b:2px\}/);
    assert.ok(!out.includes(".x"), "component rules must stay scoped");
});

test("hoists Tailwind's combined :root,:host selector", () => {
    const out = extractHoistableCss(":root,:host{--color-red-500:oklch(63% .2 25)}");
    assert.match(out, /--color-red-500/);
});

test("recurses into @layer, which is where Tailwind v4 emits @theme output", () => {
    const css = "@layer theme{:root,:host{--font-sans:x}}@layer base{body{margin:0}}";
    const out = extractHoistableCss(css);
    assert.match(out, /--font-sans:x/);
    assert.ok(!out.includes("margin:0"), "the base reset must stay scoped");
    assert.ok(!out.includes("@layer"), "a plain @layer wrapper adds nothing once hoisted");
});

test("keeps a @media condition when hoisting a conditional token override", () => {
    // src/ui/tokens.css redefines durations under prefers-reduced-motion and control
    // heights under pointer:coarse. Dropping the condition would apply them always.
    const css = "@media (prefers-reduced-motion: reduce){:root{--ada-dur-base:0ms}}";
    const out = extractHoistableCss(css);
    assert.match(out, /@media \(prefers-reduced-motion: reduce\)/);
    assert.match(out, /--ada-dur-base:0ms/);
});

test("does NOT hoist rules that merely include html among other selectors", () => {
    // `html, body { height: 100dvh }` is layout, not tokens. Hoisting it would let the
    // embed resize its host page.
    const out = extractHoistableCss("html,body{height:100dvh;overflow:hidden}");
    assert.equal(out.trim(), "", "html,body is layout and must stay scoped");
});

test("does not hoist class rules, even ones that set custom properties", () => {
    const out = extractHoistableCss(".ada-focus:focus-visible{outline:2px solid red}");
    assert.equal(out.trim(), "");
});

test("ignores braces inside strings and comments", () => {
    // A naive brace counter mis-parses content:'}' and unbalances the whole sheet.
    const css = ".a{content:'}'}:root{--x:1}";
    const out = extractHoistableCss(css);
    assert.match(out, /--x:1/);
    assert.ok(!out.includes("content"), "the class rule must not be hoisted");
});

test("buildScopedEmbedCss emits hoisted tokens BEFORE the @scope wrapper", () => {
    const out = buildScopedEmbedCss(":root{--a:1}.x{color:red}");
    const hoistAt = out.indexOf("--a:1");
    const scopeAt = out.indexOf("@scope (.ada-viewer-scope)");
    assert.ok(hoistAt >= 0 && scopeAt >= 0);
    assert.ok(hoistAt < scopeAt, "tokens must be declared before, and outside, the scope");
    // The full sheet is still emitted inside the wrapper — hoisting COPIES rather
    // than moves, so there is no chance of corrupting the stylesheet by cutting at
    // the wrong brace.
    assert.match(out.slice(scopeAt), /\.x\{color:red\}/);
});

test("a stylesheet with no token carriers is left as a plain scoped wrapper", () => {
    const out = buildScopedEmbedCss(".x{color:red}");
    assert.ok(out.startsWith("@scope (.ada-viewer-scope)"));
});

// ---------------------------------------------------------------------------
// Cascade layers. Layered styles lose to unlayered ones regardless of specificity,
// so shipping the embed's Tailwind output inside @layer let any host page's plain
// `button {}` rule beat our `.bg-*` classes. Flattening restores normal specificity.
// ---------------------------------------------------------------------------

test("flattenLayers unwraps @layer blocks but keeps their contents in order", async () => {
    // @ts-expect-error — plain .mjs build helper, no type declarations by design.
    const {flattenLayers} = await import("../../../vite.plugin-embed-css.mjs");
    const out = flattenLayers("@layer base{body{margin:0}}@layer utilities{.p-2{padding:8px}}");
    assert.ok(!out.includes("@layer"), "no layer wrapper may survive");
    assert.ok(out.indexOf("margin:0") < out.indexOf("padding:8px"), "source order must be preserved");
});

test("flattenLayers drops bare @layer order declarations", async () => {
    // @ts-expect-error — plain .mjs build helper.
    const {flattenLayers} = await import("../../../vite.plugin-embed-css.mjs");
    const out = flattenLayers("@layer theme, base, utilities;.x{color:red}");
    assert.ok(!out.includes("@layer"));
    assert.match(out, /\.x\{color:red\}/);
});

test("flattenLayers handles nested layers", async () => {
    // @ts-expect-error — plain .mjs build helper.
    const {flattenLayers} = await import("../../../vite.plugin-embed-css.mjs");
    const out = flattenLayers("@layer a{@layer b{.deep{color:red}}}");
    assert.ok(!out.includes("@layer"));
    assert.match(out, /\.deep\{color:red\}/);
});

test("the scoped embed stylesheet ships no cascade layers at all", async () => {
    // @ts-expect-error — plain .mjs build helper.
    const {buildScopedEmbedCss} = await import("../../../vite.plugin-embed-css.mjs");
    const out = buildScopedEmbedCss("@layer utilities{.bg-x{background:blue}}");
    const scoped = out.slice(out.indexOf("@scope (.ada-viewer-scope)"));
    assert.ok(!scoped.includes("@layer"), "a layered embed loses to the host page");
    assert.match(scoped, /\.bg-x\{background:blue\}/);
});

test("@media and @supports are NOT flattened", async () => {
    // Only @layer is meaningless once scoped; dropping a condition would apply its
    // rules unconditionally.
    // @ts-expect-error — plain .mjs build helper.
    const {flattenLayers} = await import("../../../vite.plugin-embed-css.mjs");
    const out = flattenLayers("@media (min-width:40px){.x{color:red}}");
    assert.match(out, /@media \(min-width:40px\)/);
});

test("CSS identifier escapes in selectors do not derail the scanner", () => {
    // Regression. Tailwind compiles a `content-['']` utility to the selector
    // `.checked\:after\:content-\[\'\'\]`. Treating that `\'` as a string delimiter put
    // the scanner into string mode for the REST OF THE FILE, swallowing every
    // subsequent brace — which silently disabled both the hoist and the flatten and
    // shipped an embed that lost every style to its host page. Escapes apply in
    // selectors, not just inside strings.
    const css =
        String.raw`.checked\:after\:content-\[\'\'\]:checked:after{content:""}` +
        ":root{--after-the-escape:1}";
    const out = extractHoistableCss(css);
    assert.match(out, /--after-the-escape:1/, "the scanner must recover after an escaped quote");
});

test("escaped braces in selectors are not counted as block delimiters", () => {
    const css = String.raw`.w-\[\{x\}\]{width:1px}` + ":root{--tok:2}";
    const out = extractHoistableCss(css);
    assert.match(out, /--tok:2/);
    assert.ok(!out.includes("width:1px"));
});
