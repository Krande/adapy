// What is drawn over the scene is drawn through the overlay manager (viewport/api/overlayManager.ts),
// so a clear of the scene, a model leaving it and a renderer switch reach all of it: the clash check's
// markers outlived a clear because they were drawn beside it. This holds the source to that: a
// viewport's layers (`overlays`, `billboards`, `images`) and its marks and fade are asked for only by
// the manager, by the viewports themselves, and by the few listed here, each with its reason.

import assert from "node:assert/strict";
import {readdirSync, readFileSync, statSync} from "node:fs";
import {join, relative} from "node:path";
import {test} from "node:test";

const SRC = join(process.cwd(), "src");
function sources(dir: string, out: string[] = []): string[] {
    for (const name of readdirSync(dir)) {
        const p = join(dir, name);
        if (statSync(p).isDirectory()) {
            if (name !== "__tests__" && name !== "node_modules") sources(p, out);
        } else if (/\.(ts|tsx)$/.test(name)) out.push(p);
    }
    return out;
}
const rel = (p: string) => relative(SRC, p).replace(/\\/g, "/");

/** Who may ask a viewport for a layer, or set its marks or fade, without the manager. */
const ALLOWED: Record<string, string> = {
    // Tools a plugin places, which follow the viewport on screen themselves and live until their
    // owner ends them (`ctx.scene.placeBox` / `placeCamera`): not something a clear takes.
    "plugins/sceneBox.ts": "a placed box: the plugin's tool",
    "plugins/sceneCamera.ts": "a placed camera: the plugin's tool",
    // The cell builder's own scene, drawn through the three.js-free viewport while it is open.
    "components/viewer/cellbuilder/nativeViewHost.ts": "the cell builder's scene",
};

const LAYER = /(?:viewport|Viewport\(\))\s*[!?]?\.\s*(?:overlays|billboards|images)\(/;
const MARKS_OR_FADE = /\.setObject(?:Marks|Fade)\b/;

/** The code of a module without its comments, which name these calls to explain them. */
const code = (text: string): string => text.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:"'`])\/\/.*$/gm, "$1");

test("nothing draws over the scene but through the overlay manager", () => {
    const offenders: string[] = [];
    for (const path of sources(SRC)) {
        const at = rel(path);
        // The viewports' own code, and the manager.
        if (at.startsWith("viewport/")) continue;
        const text = code(readFileSync(path, "utf8"));
        if (!LAYER.test(text) && !MARKS_OR_FADE.test(text)) continue;
        if (!(at in ALLOWED)) offenders.push(at);
    }
    assert.deepEqual(offenders, [], "draw over the scene through `sceneOverlays.register` (viewport/api/overlayManager.ts)");
});

test("the list of exceptions is not stale", () => {
    for (const at of Object.keys(ALLOWED)) {
        const text = code(readFileSync(join(SRC, at), "utf8"));
        assert.ok(LAYER.test(text) || MARKS_OR_FADE.test(text), `${at} no longer draws over the scene itself: take it off the list`);
    }
});
