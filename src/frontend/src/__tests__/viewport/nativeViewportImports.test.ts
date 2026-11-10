// The three.js-free viewport is only that while nothing it imports, directly or through another module,
// imports three.js. This walks the chunk's import graph from its entry and fails on the first such
// import, naming the chain that leads to it. Type-only imports are erased by the build and do not count.
import {test} from "node:test";
import assert from "node:assert/strict";
import {existsSync, readFileSync} from "node:fs";
import {dirname, join, resolve} from "node:path";

const ROOT = process.cwd();
const ENTRY = join(ROOT, "src/viewport/native/NativeViewport.tsx");
const FORBIDDEN = /^three($|\/)/;

/** Runtime import specifiers of a module: static imports and re-exports that are not type-only, and import(). */
export function runtimeImports(source: string): string[] {
    const out: string[] = [];
    const statement = /^\s*(import|export)\s+(type\s+)?([^;]*?\s+from\s+)?["']([^"']+)["']/gm;
    for (const m of source.matchAll(statement)) {
        if (m[2]) continue; // import type / export type
        if (m[1] === "export" && !m[3]) continue; // not a re-export
        out.push(m[4]);
    }
    for (const m of source.matchAll(/\bimport\(\s*(?:\/\*[^*]*\*\/\s*)?["']([^"']+)["']\s*\)/g)) out.push(m[1]);
    return out;
}

function resolveModule(from: string, specifier: string): string | null {
    const base = specifier.startsWith("@/") ? join(ROOT, "src", specifier.slice(2)) : specifier.startsWith(".") ? resolve(dirname(from), specifier) : null;
    if (!base) return null; // a package
    for (const candidate of [base, `${base}.ts`, `${base}.tsx`, join(base, "index.ts"), join(base, "index.tsx")]) {
        if (existsSync(candidate) && /\.(ts|tsx)$/.test(candidate)) return candidate;
    }
    return null;
}

/** Walk the runtime import graph from `entry`; fail on the first three.js import. Returns the module count. */
function walkFreeOfThree(entry: string): number {
    const seen = new Map<string, string | null>([[entry, null]]);
    const queue = [entry];
    const chain = (file: string): string => {
        const parts: string[] = [];
        for (let f: string | null | undefined = file; f; f = seen.get(f)) parts.unshift(f.slice(ROOT.length + 1));
        return parts.join(" -> ");
    };
    while (queue.length) {
        const file = queue.pop()!;
        for (const specifier of runtimeImports(readFileSync(file, "utf-8"))) {
            assert.ok(!FORBIDDEN.test(specifier), `${chain(file)} imports "${specifier}"`);
            const next = resolveModule(file, specifier);
            if (next && !seen.has(next)) {
                seen.set(next, file);
                queue.push(next);
            }
        }
    }
    return seen.size;
}

test("the three.js-free viewport's chunk imports no three.js", () => {
    const walked = walkFreeOfThree(ENTRY);
    assert.ok(walked >= 6, `only ${walked} modules walked: the walk must reach the scene, the renderer start-up and the viewport interface`);
});

// The interface both viewports stand behind, and the keys bound through it, are loaded by the
// three.js-free viewport: they may name three.js in types only.
for (const module of ["viewportApi.ts", "viewportShortcuts.ts", "billboards.ts", "overlayLayer.ts", "beforeDraw.ts"]) {
    test(`viewport/api/${module} imports no three.js`, () => {
        walkFreeOfThree(join(ROOT, "src/viewport/api", module));
    });
}

// What computes and registers an FEA result is shared by both viewports, and what draws it through
// result layers is the three.js-free viewport's way to show one: none of it may import three.js.
for (const module of [
    "viewport/api/resultLayer.ts",
    "viewport/native/nativeResultLayer.ts",
    "viewport/native/resultObjects.ts",
    "utils/glb/glbMesh.ts",
    "utils/scene/fea/nodalFieldPaint.ts",
    "utils/scene/fea/elemFieldPaint.ts",
    "utils/scene/fea/labelPlacement.ts",
    "utils/scene/fea/streaming/layerGroup.ts",
    "utils/scene/fea/streaming/layerBeamSolids.ts",
    "utils/scene/fea/streaming/prefetch.ts",
    "utils/scene/fea/resultInfluence.ts",
    "utils/scene/fea/feaAnimationDriver.ts",
    "utils/scene/fea/playbackActions.ts",
    "utils/scene/fea/streaming/resultStores.ts",
    "utils/scene/fea/streaming/warpSource.ts",
    "utils/scene/fea/streaming/layerSession.ts",
    // What is drawn over a result, and what plugins draw through: plain data on both viewports.
    "utils/scene/fea/elementSource.ts",
    "utils/scene/fea/streaming/layerOverlayTarget.ts",
    "viewport/native/nativeOverlays.ts",
    "plugins/sceneOverlays.ts",
]) {
    test(`${module} imports no three.js`, () => {
        walkFreeOfThree(join(ROOT, "src", module));
    });
}

// Selection boxes are plain data, and what a box selects is asked through the viewport interface:
// the store, the rules, the handles' maths and applying a box are shared by both viewports.
for (const module of [
    "viewport/api/boxSelection.ts",
    "viewport/native/boxSelectionGizmo.ts",
    "viewport/native/nativeBoxSelection.ts",
    "state/bboxSelectionStore.ts",
    "utils/bboxSelection/applyBoxSelection.ts",
    "utils/bboxSelection/boxHandles.ts",
    "utils/bboxSelection/boxMath.ts",
    "utils/bboxSelection/marquee.ts",
    "utils/bboxSelection/screenRect.ts",
]) {
    test(`${module} imports no three.js`, () => {
        walkFreeOfThree(join(ROOT, "src", module));
    });
}

test("the walk sees what a build would bundle", () => {
    const imports = runtimeImports(`
        import * as THREE from "three";
        import type {Scene} from "three";
        import {a} from "./a";
        export {b} from "./b";
        export type {C} from "./c";
        export const d = 1;
        const lazy = () => import("./lazy");
        const ignored = () => import(/* @vite-ignore */ "./ignored");
    `);
    assert.deepEqual(imports.sort(), ["./a", "./b", "./ignored", "./lazy", "three"]);
});
