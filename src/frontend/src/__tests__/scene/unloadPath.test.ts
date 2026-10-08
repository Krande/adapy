/**
 * One unload path: every caller unloads a model through `unload_any_source`, never through the
 * per-source teardown it routes to -- a caller that did would skip the streaming-FEA teardown.
 */

import assert from "node:assert/strict";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative, sep } from "node:path";
import { test } from "node:test";

const SRC = join(import.meta.dirname, "..", "..");
const ROUTER = join("utils", "scene", "handlers", "unload_any_source.ts");
const IMPL = join("utils", "scene", "handlers", "unload_source_from_scene.ts");

function* sources(dir: string): Generator<string> {
  for (const name of readdirSync(dir)) {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) {
      if (name === "__tests__" || name === "node_modules") continue;
      yield* sources(path);
    } else if (/\.(ts|tsx)$/.test(name)) {
      yield path;
    }
  }
}

test("only unload_any_source calls the per-source teardown", () => {
  const offenders: string[] = [];
  for (const path of sources(SRC)) {
    const rel = relative(SRC, path);
    if (rel === ROUTER || rel === IMPL) continue;
    const code = readFileSync(path, "utf8");
    // A call or an import, not a mention in a comment.
    if (/unload_source_from_scene\s*\(|handlers\/unload_source_from_scene["']/.test(code)) offenders.push(rel.split(sep).join("/"));
  }
  assert.deepEqual(offenders, [], `unload through unload_any_source instead: ${offenders.join(", ")}`);
});
