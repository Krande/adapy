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
import { createHash } from "node:crypto";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { test } from "node:test";

const SRC = join(process.cwd(), "src");

/** Vendor systems and out-of-tree package names, as SHA-256 of the lower-cased term. Not domain words — see the header.
 *  Stored hashed so the list doesn't spell the terms out; it is not meant to resist guessing. To add one, add the hash
 *  of its lower-cased form (`printf %s term | sha256sum`). */
const FORBIDDEN_SHA256 = new Set([
  "3f4f8bb2ab748f19e4104c9dcb4e445ab333ff1f5186e4e8f0a8152869308994",
  "5a669d12ed3919433eede6a90769c4288c46bbd607a6ebefff216b60642f10d5",
  "5fe569f4011f206147df7550d6e2b7d8dc1e275c303676a65d15d053aefdfca4",
  "78e68846dc98caf934eabb89dbb0f4bc95e6d6741e9edccaffdf8895bb889e5e",
  "7f21dddbe3e180974dbdf79537b5a19e0e7f62b85e3892128f074c69c80be956",
  "918557e3886df3a81f01019a6906da83c05b15836b33e09fc557569049e66cef",
  "91f7aec9d86e5b329e257330b0eeb28773d4112f274d4ffd9134fb2100948287",
  "9540ba9f2a635fdd7e768b911d626d0f3cdfbccab4df5b397d6ced914753c2a4",
  "96aed7c729899185bf13863acea99b958f81be3d5222ea709d49aa0af3e7446b",
  "981309a55b1a205ad3b78139f9b8004d9817e5e60c726cdcc3d44792b8e5de1c",
  "9fe6746863d6b21cff9dcfaf974e0192858b0c582888685307228527cb695b32",
  "cb62d62cda97ab93bfaa8f7fd285520ae3132dc3033c4194ba53cd8e417dd876",
]);

/** This file's fixtures name public vendor systems, so the walk leaves it out. */
const SELF = "vocabularyGate.test.ts";

const EXTS = [".ts", ".tsx"];

/** Every term a line could contain, as `\b(term)\b` would see it: each word, and each run of consecutive words
 *  joined by `-`. `_` stays inside a word. */
function candidateTerms(line: string): string[] {
  const out: string[] = [];
  for (const token of line.toLowerCase().match(/[a-z0-9_]+(?:-[a-z0-9_]+)*/g) ?? []) {
    const parts = token.split("-");
    for (let start = 0; start < parts.length; start++) {
      for (let end = start + 1; end <= parts.length; end++) out.push(parts.slice(start, end).join("-"));
    }
  }
  return out;
}

const sha256 = (text: string) => createHash("sha256").update(text).digest("hex");

/** Whether a line names a forbidden term. */
function namesForbidden(line: string): boolean {
  return candidateTerms(line).some((t) => FORBIDDEN_SHA256.has(sha256(t)));
}

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

  const hits: string[] = [];
  for (const file of files) {
    readFileSync(file, "utf-8")
      .split("\n")
      .forEach((line, i) => {
        if (namesForbidden(line)) hits.push(`${file.slice(SRC.length + 1)}:${i + 1}: ${line.trim()}`);
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
  assert.ok(namesForbidden("// exported from AVEVA E3D"), "the gate must match a real mention");
  assert.ok(namesForbidden('id: "csg"'), "the gate must match an identifier fixture");
  assert.ok(candidateTerms("pkg-alpha-beta-ui").includes("alpha-beta"), "a run inside a hyphenated name must be checked");
  assert.ok(!namesForbidden("const weld = (v: number) => v"), "vertex welding must stay legal");
  assert.ok(!namesForbidden("Welds (3)"), "rendering a provider's welds must stay legal");
  assert.ok(!namesForbidden("const csg_mesh = 1"), "a term inside a longer identifier must not match, as with \\b");
});
