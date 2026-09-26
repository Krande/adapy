// The frontend half of the vocabulary gate (`tests/core/clash/test_vocabulary_gate.py` is the
// backend's). This bundle is PUBLIC, and it is also a plugin host: the thing that makes the plugin
// API worth having is that core does not know which plugins exist, so a core file naming one is
// both a leak and a design smell — it says the seam had a particular consumer in mind, and the next
// reader writes the next branch for it.
//
// WHAT IS FORBIDDEN HERE IS NARROWER THAN ON THE BACKEND, deliberately. The Python gate bans the
// word `weld` inside `ada.clash`, because identification must not know about a fabrication process.
// This bundle has no such rule to protect: it RENDERS whatever a provider produced, so a
// Connections panel that shows welds is doing its job, and `EdgeShaderHelper` welds vertices in the
// ordinary mesh sense. Banning the word here would force worse names on honest code.
//
// So this list is identifiers: vendor systems core deliberately cannot read, and the names of
// out-of-tree packages. A format core genuinely implements is not on it — naming a format you can
// write is documentation.
//
// Scans the SOURCE TREE rather than the built bundle: a term in a comment ships in the sourcemap,
// and the point is to keep it out of the repository at all.

import assert from "node:assert/strict";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { test } from "node:test";

const SRC = join(process.cwd(), "src");

/** Vendor systems and out-of-tree package names. Not domain words — see the header. */
const FORBIDDEN = [
  "aveva",
  "e3d",
  "tekla",
  "csg",
  "weld-gen",
  "weldgen",
  "web3d",
  "codecheck",
  "param_models",
  "parametric_models",
  "aibel",
  "tapps",
];

/** This file states the terms, so it is the one file that may contain them. */
const SELF = "vocabularyGate.test.ts";

const EXTS = [".ts", ".tsx"];

function walk(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    if (entry === "node_modules" || entry.startsWith(".")) continue;
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) walk(full, out);
    else if (EXTS.some((e) => entry.endsWith(e)) && entry !== SELF) out.push(full);
  }
  return out;
}

test("no core frontend file names a vendor system or an out-of-tree package", () => {
  const files = walk(SRC);
  assert.ok(files.length > 100, `only ${files.length} files walked — a gate that read nothing proves nothing`);

  const pattern = new RegExp(`\\b(${FORBIDDEN.join("|")})\\b`, "i");
  const hits: string[] = [];
  for (const file of files) {
    readFileSync(file, "utf-8")
      .split("\n")
      .forEach((line, i) => {
        if (pattern.test(line)) hits.push(`${file.slice(SRC.length + 1)}:${i + 1}: ${line.trim()}`);
      });
  }
  assert.deepEqual(
    hits,
    [],
    `a core frontend file names a vendor system or an out-of-tree package:\n  ${hits.join("\n  ")}\n\n` +
      "Describe the SHAPE of the thing — a private format, an out-of-tree plugin, a capability " +
      "tag — never whose it is.",
  );
});

test("the gate would catch a term if one appeared", () => {
  // The self-check the backend gate has for the same reason: a walker that silently matched
  // nothing, or an exclusion that grew to cover everything, passes while proving nothing.
  const pattern = new RegExp(`\\b(${FORBIDDEN.join("|")})\\b`, "i");
  assert.ok(pattern.test("// exported from AVEVA E3D"), "the pattern must match a real mention");
  assert.ok(pattern.test('id: "csg"'), "the pattern must match an identifier fixture");
  assert.ok(!pattern.test("const weld = (v: number) => v"), "vertex welding must stay legal");
  assert.ok(!pattern.test("Welds (3)"), "rendering a provider's welds must stay legal");
});
