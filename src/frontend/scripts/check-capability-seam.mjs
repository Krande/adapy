#!/usr/bin/env node
// Guards the transport capability seam (src/services/capabilities).
//
// The stores and the scene/loader layer must reach the backend through
// `capabilities.<domain>` -- one interface, a REST and a websocket
// implementation, selected at runtime -- never through the REST-only HTTP
// client `services/viewerApi` directly. A direct call there is exactly the
// bug the seam exists to stop: a feature that silently does not exist on the
// websocket (`assembly.show()`) path. This script fails the build for any
// RUNTIME import of viewerApi under the guarded directories. Type-only imports
// of its DTOs (`import type {...}`, inline `type` specifiers, `typeof
// import(...)`) are fine: they are erased at compile time and bind nothing.
//
// Run: `node scripts/check-capability-seam.mjs` (wired as `npm run check:seam`
// and as the `pretest` hook, so `npm test` runs it first). Optional
// `--root <dir>` scans that directory's `state`, `utils/scene` and
// `components/viewer/sceneHelpers` instead of src/ (used by the unit test).
//
// No dependencies: a TypeScript parser would be more precise, but the two
// import forms below are the only ones the codebase uses and the failure mode
// of the regexes is a false POSITIVE that a `type` modifier fixes -- which is
// the discipline the seam wants anyway.

import { readdirSync, readFileSync, statSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));

/** Directories (relative to the root) whose files may not import viewerApi at runtime. */
const GUARDED_DIRS = ["state", "utils/scene", "components/viewer/sceneHelpers"];

/** Files (relative to the root, forward slashes) exempted from the check.
 * Deliberately empty: every store and scene module is on the seam. Add an
 * entry only with a comment saying which capability it is waiting for. */
const ALLOW_LIST = new Set([]);

/** The module specifier forms that resolve to services/viewerApi. */
const VIEWER_API_SPEC = /^(?:@\/|(?:\.\.\/)+|\.\/)?(?:[\w.-]+\/)*services\/viewerApi(?:\.ts)?$/;

function parseArgs(argv) {
  let root = resolve(__dirname, "..", "src");
  for (let i = 0; i < argv.length; i++) {
    if (argv[i] === "--root") root = resolve(argv[++i]);
  }
  return { root };
}

function* walk(dir) {
  let entries;
  try {
    entries = readdirSync(dir);
  } catch {
    return;
  }
  for (const name of entries) {
    const p = join(dir, name);
    const st = statSync(p);
    if (st.isDirectory()) yield* walk(p);
    else if (/\.(ts|tsx|js|jsx|mjs)$/.test(name) && !/\.d\.ts$/.test(name)) yield p;
  }
}

/** Blank out comments while keeping line numbers (newlines survive). */
function stripComments(src) {
  return src
    .replace(/\/\*[\s\S]*?\*\//g, (m) => m.replace(/[^\n]/g, " "))
    .replace(/(^|[^:'"`])\/\/[^\n]*/g, (m, lead) => lead + " ".repeat(m.length - lead.length));
}

function lineOf(src, index) {
  return src.slice(0, index).split("\n").length;
}

/** Every runtime import of viewerApi in `src`, as {line, reason}. */
export function findRuntimeViewerApiImports(src) {
  const clean = stripComments(src);
  const hits = [];

  // Static imports / re-exports: `import [type] <clause> from "spec"`.
  const staticRe = /\b(import|export)\s+(type\s+)?([^;'"]*?)\s*from\s*["']([^"']+)["']/g;
  let m;
  while ((m = staticRe.exec(clean)) !== null) {
    const [, , typeKw, clause, spec] = m;
    if (!VIEWER_API_SPEC.test(spec)) continue;
    if (typeKw) continue; // `import type {...}` -- erased
    const braces = clause.match(/^\{([\s\S]*)\}$/);
    if (!braces) {
      hits.push({ line: lineOf(clean, m.index), reason: `runtime ${m[1]} of viewerApi (${clause.trim()})` });
      continue;
    }
    const runtimeNames = braces[1]
      .split(",")
      .map((s) => s.trim())
      .filter((s) => s.length > 0 && !/^type\s+/.test(s));
    if (runtimeNames.length > 0) {
      hits.push({
        line: lineOf(clean, m.index),
        reason: `runtime import of {${runtimeNames.join(", ")}} from viewerApi (add the \`type\` modifier if these are DTOs)`,
      });
    }
  }

  // Dynamic imports: `import("spec")`, unless it is a `typeof import(...)` type query.
  const dynamicRe = /(typeof\s+)?\bimport\s*\(\s*["']([^"']+)["']\s*\)/g;
  while ((m = dynamicRe.exec(clean)) !== null) {
    const [, typeofKw, spec] = m;
    if (!VIEWER_API_SPEC.test(spec)) continue;
    if (typeofKw) continue;
    hits.push({ line: lineOf(clean, m.index), reason: "dynamic import() of viewerApi" });
  }

  return hits;
}

function main() {
  const { root } = parseArgs(process.argv.slice(2));
  const violations = [];
  let scanned = 0;
  for (const dir of GUARDED_DIRS) {
    for (const file of walk(join(root, dir))) {
      const rel = relative(root, file).split("\\").join("/");
      if (ALLOW_LIST.has(rel)) continue;
      scanned += 1;
      const src = readFileSync(file, "utf8");
      for (const hit of findRuntimeViewerApiImports(src)) {
        violations.push(`${rel}:${hit.line}: ${hit.reason}`);
      }
    }
  }
  if (violations.length > 0) {
    console.error("capability seam violated -- these files must go through `capabilities.<domain>` (src/services/capabilities):");
    for (const v of violations) console.error(`  ${v}`);
    console.error(`${violations.length} runtime import(s) of services/viewerApi under ${GUARDED_DIRS.join(", ")}.`);
    process.exit(1);
  }
  console.log(`capability seam OK: ${scanned} files under ${GUARDED_DIRS.join(", ")} import no viewerApi at runtime.`);
}

// Run only when executed directly (the unit test imports the finder).
if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main();
}
