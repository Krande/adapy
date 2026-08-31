// Enforces the `@/viewer-core` fence — the thing that makes the facade a
// contract rather than a suggestion.
//
// Three rules, each a failure mode we would otherwise only discover when an
// out-of-tree UI repo broke on an adapy release:
//
//   1. A plugin package (feature plugin or UI shell) imports core ONLY through
//      `@/viewer-core*`. Deep imports are unbounded coupling: every internal
//      rename becomes a breaking change for a repo we cannot grep.
//   2. The facade re-exports from LEAF modules — never `@/plugins`, whose
//      loader barrel imports the plugin packages, which import the facade. That
//      cycle would bite at module-init time, in the browser, at boot.
//   3. The root entry point stays dependency-free, and the heavier surface stays
//      behind /app, /scene and /plugins — so a canvas-less shell profile does not
//      drag the 3D code into its chunk, and a plugin that only declares itself
//      can be unit-tested with no DOM.
//
// The allowlist exists for a deliberate, reviewed exception; it is empty, and a
// non-empty entry should carry a reason in the PR that adds it.

import assert from "node:assert/strict";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { dirname, join, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";
import { test } from "node:test";

const here = dirname(fileURLToPath(import.meta.url));
const frontendRoot = resolve(here, "../../..");
const pluginsDir = join(frontendRoot, "packages/plugins");
const facadeDir = join(frontendRoot, "src/viewer-core");

const allowlist: string[] = JSON.parse(
  readFileSync(join(here, "viewerCoreImports.allowlist.json"), "utf8"),
);

function walk(dir: string): string[] {
  const out: string[] = [];
  for (const name of readdirSync(dir)) {
    if (name === "node_modules" || name === "dist") continue;
    const p = join(dir, name);
    if (statSync(p).isDirectory()) out.push(...walk(p));
    else if (/\.(ts|tsx)$/.test(name)) out.push(p);
  }
  return out;
}

/** Every module specifier in `from "…"` / `import("…")` position. */
function importSpecifiers(src: string): string[] {
  const out: string[] = [];
  for (const m of src.matchAll(/\bfrom\s+["']([^"']+)["']/g)) out.push(m[1]);
  for (const m of src.matchAll(/\bimport\s*\(\s*["']([^"']+)["']\s*\)/g)) out.push(m[1]);
  return out;
}

// Normalised to forward slashes: `join`/`walk` hand back the platform separator,
// so on Windows every path here came out `src\viewer-core\index.ts` and the
// assertions -- which spell the expected paths the POSIX way, as the repo does
// everywhere else -- failed on a tree with nothing wrong with it.
const rel = (p: string) => p.slice(frontendRoot.length + 1).split(sep).join("/");

test("plugin packages import core only through @/viewer-core", () => {
  const offenders: string[] = [];
  for (const file of walk(pluginsDir)) {
    if (allowlist.includes(rel(file))) continue;
    for (const spec of importSpecifiers(readFileSync(file, "utf8"))) {
      if (!spec.startsWith("@/")) continue; // relative + npm deps are the plugin's own business
      if (spec === "@/viewer-core" || spec.startsWith("@/viewer-core/")) continue;
      offenders.push(`${rel(file)} -> ${spec}`);
    }
  }
  assert.deepEqual(
    offenders,
    [],
    "plugin packages must import core through @/viewer-core (add the export to the " +
      "facade, or the file to viewerCoreImports.allowlist.json with a reason):\n" +
      offenders.join("\n"),
  );
});

test("the facade re-exports leaf modules, never the @/plugins barrel (cycle guard)", () => {
  const offenders: string[] = [];
  for (const file of walk(facadeDir)) {
    for (const spec of importSpecifiers(readFileSync(file, "utf8"))) {
      if (spec === "@/plugins") offenders.push(`${rel(file)} -> ${spec}`);
    }
  }
  assert.deepEqual(
    offenders,
    [],
    "@/plugins imports registry.generated -> the plugin packages -> this facade; " +
      "re-export the leaf module instead:\n" + offenders.join("\n"),
  );
});

test("the root @/viewer-core entry point is dependency-free", async () => {
  // The proof is the import itself: this runs under plain `node --test` with no
  // DOM, so anything that reaches a store, `window` or three throws here. That is
  // what keeps a plugin which merely DECLARES itself headless-testable — the same
  // property @/plugins/registry is written for.
  const facade = await import("@/viewer-core");
  assert.equal(typeof facade.registerPlugin, "function");
  assert.equal(typeof facade.listUiShells, "function");
  assert.equal(typeof facade.VIEWER_CORE_API_VERSION, "string");
});

test("the root entry point re-exports only the dependency-free leaves", () => {
  // Belt to the previous test's braces: node might tolerate a heavy module that
  // a browser build would still pay for. Anything else belongs in /app, /scene
  // or /plugins.
  const DEP_FREE_LEAVES = ["@/plugins/registry", "@/plugins/uiShells"];
  const specs = importSpecifiers(readFileSync(join(facadeDir, "index.ts"), "utf8"));
  const offenders = specs.filter((s) => !DEP_FREE_LEAVES.includes(s));
  assert.deepEqual(
    offenders,
    [],
    `@/viewer-core may only re-export ${DEP_FREE_LEAVES.join(", ")}; move these to ` +
      "@/viewer-core/app, /scene or /plugins:\n" + offenders.join("\n"),
  );
});

test("every facade entry point declares the contract version exactly once", () => {
  const decls = walk(facadeDir).filter((f) =>
    /export const VIEWER_CORE_API_VERSION/.test(readFileSync(f, "utf8")),
  );
  assert.deepEqual(decls.map(rel), ["src/viewer-core/index.ts"]);
});
