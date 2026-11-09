// Every Options setting is a command, from one list; the palette finds them by name, keyword and id.

import assert from "node:assert/strict";
import { test } from "node:test";
import { readFileSync, readdirSync } from "node:fs";

await import("../fixtures/panelRealm");

const C = await import("@/layout/commands");
const O = await import("@/layout/optionCommands");
const { registerCoreCommands } = await import("@/layout/coreCommands");
const { useOptionsStore } = await import("@/state/optionsStore");
const { useComputeStore } = await import("@/state/computeStore");

const setup = () => {
  C.resetCommands();
  registerCoreCommands();
};

test("the palette finds a setting by name, keyword, id, partial, case and several words", () => {
  setup();
  const mouse = C.getCommand("core:option.spacemouse")!;
  assert.ok(mouse, "registered");
  for (const q of ["spacemouse", "space mouse", "3d mouse", "3dconnexion", "SPACEMOUSE", "spacem", "3d mouse enable", "option.spacemouse", "3dconn"]) {
    assert.notEqual(C.commandRank(q, mouse), null, q);
  }
  assert.equal(C.commandRank("antialiasing xyz", mouse), null, "every word must match");
  assert.equal(C.commandRank("", mouse), 0);
  const aa = C.getCommand("core:option.antialias")!;
  assert.notEqual(C.commandRank("msaa", aa), null);
  assert.notEqual(C.commandRank("dpr", C.getCommand("core:option.pixel-ratio")!), null);
  // A better label match ranks ahead of a keyword hit.
  assert.ok(C.commandRank("3d mouse", mouse)! <= C.commandRank("3dconnexion", mouse)!);
});

test("a toggle flips its setting and shows it checked; a choice has one command per value", () => {
  setup();
  useOptionsStore.getState().setShowMeshStats(true);
  const cmd = C.getCommand("core:option.mesh-stats")!;
  assert.equal(cmd.checked!(), true);
  C.runCommand(cmd.id);
  assert.equal(useOptionsStore.getState().showMeshStats, false);
  assert.equal(cmd.checked!(), false);
  C.runCommand(cmd.id);
  assert.equal(useOptionsStore.getState().showMeshStats, true);

  useComputeStore.getState().setMode("balanced");
  assert.equal(C.getCommand("core:option.compute-mode.balanced")!.checked!(), true);
  C.runCommand("core:option.compute-mode.throughput");
  assert.equal(useComputeStore.getState().mode, "throughput");
  assert.equal(C.getCommand("core:option.compute-mode.throughput")!.checked!(), true);
  assert.equal(C.getCommand("core:option.compute-mode.balanced")!.checked!(), false);
  useComputeStore.getState().reset();
});

test("the 3D mouse toggle needs WebHID and says so", () => {
  setup();
  assert.match(C.getCommand("core:option.spacemouse")!.disabledReason?.() ?? "", /WebHID/);
  assert.ok(C.getCommand("core:option.spacemouse-connect"));
});

test("running the 3D mouse command asks for the device inside the key press or click, before anything is awaited", () => {
  const src = readFileSync(new URL("../../layout/optionCommands.ts", import.meta.url), "utf8");
  // Enabling and the connect command call requestSpaceMouse synchronously...
  const toggle = src.slice(src.indexOf('id: "spacemouse"'), src.indexOf('id: "spacemouse-dominant"'));
  assert.match(toggle, /sm\(\)\.setEnabled\(on\);[\s\S]*requestSpaceMouse\(\)/);
  assert.doesNotMatch(toggle, /await |\.then\(/);
  const connect = src.slice(src.indexOf('id: "core:option.spacemouse-connect"'), src.indexOf('id: "core:option.compute-reset"'));
  assert.match(connect, /requestSpaceMouse\(\)/);
  assert.doesNotMatch(connect, /await |\.then\(/);
  // ...and the palette runs the command in the same turn it closes itself, without awaiting.
  const palette = readFileSync(new URL("../../layout/CommandPalette.tsx", import.meta.url), "utf8");
  const choose = palette.slice(palette.indexOf("const choose ="), palette.indexOf("return (", palette.indexOf("const choose =")));
  assert.match(choose, /onClose\(\);\s*run\(c\);/);
  assert.doesNotMatch(choose, /await|async|setTimeout|\.then\(/);
  // The device module's request does its first await only on requestDevice itself.
  const dev = readFileSync(new URL("../../utils/spacemouse/spaceMouseDevice.ts", import.meta.url), "utf8");
  const req = dev.slice(dev.indexOf("export async function requestSpaceMouse"));
  assert.ok(req.indexOf("hid.requestDevice") < req.indexOf("await openDevice"));
  assert.equal((req.slice(0, req.indexOf("hid.requestDevice")).match(/await/g) ?? []).length, 0);
});

test("AUDIT: every setter an Options component calls has a command", () => {
  const dir = new URL("../../components/options/", import.meta.url);
  const covered = new Set<string>([
    ...O.optionDescriptors().flatMap((d) => d.setters),
    ...Object.keys(O.EXISTING_COVERAGE),
    ...O.NOT_SETTINGS,
    "setTimeout", "setInterval", "setImmediate",
  ]);
  const missing: string[] = [];
  for (const f of readdirSync(dir).filter((n) => n.endsWith(".tsx"))) {
    // Skip what is not a setting: the keyboard dialog (its own editor) and REST-only account rows.
    if (f === "ShortcutsModal.tsx" || f === "RestSection.tsx" || f === "ActionButtons.tsx") continue;
    const src = readFileSync(new URL(f, dir), "utf8");
    const local = new Set([...src.matchAll(/\[\s*\w+\s*,\s*(set\w+)\s*\]\s*=\s*(?:React\.)?useState/g)].map((m) => m[1]));
    for (const m of src.matchAll(/\bset[A-Z]\w*/g)) {
      if (!covered.has(m[0]) && !local.has(m[0])) missing.push(`${f}: ${m[0]}`);
    }
  }
  assert.deepEqual([...new Set(missing)], [], "add a descriptor in layout/optionCommands.ts for each of these");
});

test("every descriptor registers a command with a unique id, and the existing coverage names real commands", () => {
  setup();
  const ids = O.optionCommandSpecs().map((s) => s.id);
  assert.equal(new Set(ids).size, ids.length);
  for (const id of ids) assert.ok(C.getCommand(id), id);
  for (const [setter, cmd] of Object.entries(O.EXISTING_COVERAGE)) {
    if (cmd.includes("<")) continue;
    assert.ok(C.getCommand(cmd), `${setter} -> ${cmd}`);
  }
});
