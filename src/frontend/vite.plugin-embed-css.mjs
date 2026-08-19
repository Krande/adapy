// Splits the embed's compiled stylesheet into the part that must live OUTSIDE the
// `@scope (.ada-viewer-scope)` wrapper and the part that must stay inside.
//
// The problem: the embed wraps all its CSS in `@scope (.ada-viewer-scope) {...}` so its
// Tailwind reset can't clobber the host page. But `@scope` only matches DESCENDANTS of
// the scope root, and `<html>` is not a descendant of the mount element — so a
// `:root { --token: ... }` rule inside the wrapper matches nothing and every custom
// property silently resolves to its fallback. The embed renders unstyled.
//
// The fix: custom-property declarations (`:root` / `:host` rules — Tailwind v4's
// `@theme` output plus src/ui/tokens.css) are COPIED out to the top level, where they
// apply to <html> and inherit down into the scope. Everything else — the reset, the
// utilities, the component rules — stays inside, where the containment is the point.
//
// Copies rather than moves: leaving the originals in place costs a few hundred bytes
// of dead rules and removes any chance of corrupting the stylesheet by cutting at the
// wrong brace. Custom properties are inherited, so the outer copy is what wins.
//
// Runtime COLOUR tokens are not affected either way — themeStore writes those onto an
// element's inline style precisely because of this @scope behaviour. See
// src/ui/themeTokens.ts.

import fs from "node:fs";

/** Selectors whose declarations are safe (and necessary) to hoist. */
const HOISTABLE = /^(:root|:host|html)$/;

/**
 * Is every selector in this comma-separated list hoistable?
 * `:root, :host` → yes. `html, body` → no (that's layout, it must stay scoped).
 */
function isHoistableSelector(prelude) {
    const sels = prelude.split(",").map((s) => s.trim()).filter(Boolean);
    if (sels.length === 0) return false;
    return sels.every((s) => HOISTABLE.test(s));
}

/**
 * Split `css` into top-level blocks of {prelude, body, raw}, respecting nesting,
 * strings and comments. Declarations outside any block are returned as raw text.
 */
function topLevelBlocks(css) {
    const out = [];
    let depth = 0;
    let start = 0;
    let preludeEnd = -1;
    let inString = null;
    let inComment = false;

    for (let i = 0; i < css.length; i++) {
        const c = css[i];
        const next = css[i + 1];

        if (inComment) {
            if (c === "*" && next === "/") { inComment = false; i++; }
            continue;
        }
        if (inString) {
            if (c === "\\") { i++; continue; }
            if (c === inString) inString = null;
            continue;
        }
        if (c === "/" && next === "*") { inComment = true; i++; continue; }
        // CSS escapes apply in SELECTORS too, not only inside strings. Tailwind emits
        // `.checked\:after\:content-\[\'\'\]` for a `content-['']` utility; treating
        // that `\'` as a string delimiter put the scanner into string mode for the rest
        // of the file and swallowed every following brace — which silently disabled
        // both the hoist and the flatten. Skip the escaped character.
        if (c === "\\") { i++; continue; }
        if (c === '"' || c === "'") { inString = c; continue; }

        if (c === "{") {
            if (depth === 0) preludeEnd = i;
            depth++;
        } else if (c === "}") {
            depth--;
            if (depth === 0) {
                out.push({
                    prelude: css.slice(start, preludeEnd).trim(),
                    body: css.slice(preludeEnd + 1, i),
                    raw: css.slice(start, i + 1),
                });
                start = i + 1;
            }
        }
    }
    return out;
}

/**
 * Collect the CSS text that must be emitted outside the @scope wrapper.
 *
 * Recurses one level into grouping at-rules (`@layer`, `@media`, `@supports`) because
 * Tailwind v4 emits its `@theme` output inside `@layer`. A `@media`-wrapped set of
 * hoistable rules keeps its `@media` condition; `@layer` is dropped, since layer
 * ordering is irrelevant for custom properties that nothing else declares.
 *
 * Exported for the unit test.
 */
export function extractHoistableCss(css) {
    const parts = [];

    for (const block of topLevelBlocks(css)) {
        const {prelude, body, raw} = block;

        if (prelude.startsWith("@")) {
            const at = prelude.split(/\s|\(/)[0].toLowerCase();
            if (at === "@layer" || at === "@media" || at === "@supports" || at === "@container") {
                const inner = extractHoistableCss(body);
                if (!inner.trim()) continue;
                // Preserve a conditional wrapper; a plain @layer adds nothing here.
                parts.push(at === "@layer" ? inner : `${prelude} {\n${inner}\n}`);
            }
            // Other at-rules (@charset, @import, @font-face, @keyframes, @property…)
            // are not custom-property carriers — leave them scoped.
            continue;
        }

        if (isHoistableSelector(prelude)) parts.push(raw);
    }

    return parts.join("\n");
}

/**
 * Remove `@layer` wrappers, keeping their contents in source order.
 *
 * Cascade layers lose to unlayered styles UNCONDITIONALLY — specificity is not even
 * considered. Tailwind v4 emits everything inside `@layer properties/theme/base/
 * components/utilities`, so in the embed a host page's plain `button { background:
 * #c00 }` beat our `.bg-blue-700` class and deformed every control. (Observed in
 * embed/dev.html: the toolbar rendered in the host's red pill styling.)
 *
 * Converting components to design-system primitives does NOT fix this — those are
 * Tailwind utilities in `@layer utilities` too. Flattening is the fix.
 *
 * Safe because layer order and source order agree here: Tailwind emits theme → base →
 * components → utilities, so dropping the wrappers preserves relative precedence for
 * equal-specificity rules, while class-based utilities go back to beating the host's
 * element selectors the ordinary way.
 *
 * Only applied to the embed. The standalone app owns its whole document and wants
 * Tailwind's normal layering.
 */
export function flattenLayers(css) {
    // `@layer a, b, c;` statements only declare order — meaningless once flattened.
    let out = css.replace(/@layer[^{;]*;/g, "");

    // Unwrap `@layer name { … }` blocks, innermost-last, until none remain.
    for (;;) {
        const blocks = topLevelBlocks(out);
        const layers = blocks.filter((b) => /^@layer\b/.test(b.prelude));
        if (layers.length === 0) break;
        for (const b of layers) out = out.replace(b.raw, b.body);
    }
    return out;
}

/**
 * Wrap a compiled stylesheet for the embed: hoisted custom properties first, then
 * everything else inside `@scope (.ada-viewer-scope)`.
 *
 * Exported for the unit test.
 */
export function buildScopedEmbedCss(css) {
    // Escape hatch for diagnosing this transform against the real compiled stylesheet:
    //   ADA_EMBED_CSS_DUMP=/path/to/out.css npm run build:embed
    // Written synchronously: an async write can lose the race with process exit.
    if (process.env.ADA_EMBED_CSS_DUMP) {
        fs.writeFileSync(process.env.ADA_EMBED_CSS_DUMP, css, "utf8");
    }
    const hoisted = extractHoistableCss(css);
    const scoped = `@scope (.ada-viewer-scope) {\n${flattenLayers(css)}\n}\n`;
    if (!hoisted.trim()) return scoped;
    return (
        `/* ada embed: custom properties hoisted out of @scope — a :root rule inside\n` +
        `   @scope matches nothing, so scoping these would unstyle the embed. */\n` +
        `${hoisted}\n\n${scoped}`
    );
}
