// The rail's Storage button opens a docked panel in every Desktop mode, and the source tree holds no
// mis-encoded text (UTF-8 read as a single-byte codepage turns "×" into "Ã—").

import assert from "node:assert/strict";
import { test } from "node:test";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";

await import("../fixtures/panelRealm");

const D = await import("@/layout/desktop/desktopShell");
const { CORE_PANEL_IDS } = await import("@/layout/corePanelIds");
const { gridGroups } = await import("@/layout/splitTree");

test("Storage is on the rail, right after Open", () => {
  const i = D.DESKTOP_RAIL_TOOLS.indexOf(`panel:${CORE_PANEL_IDS.storage}`);
  assert.ok(i > 0);
  assert.equal(D.DESKTOP_RAIL_TOOLS[i - 1], "core:open-file");
});

test("every Desktop mode has a dock that holds Storage, so revealing it never floats it", () => {
  const spec = D.desktopUiShellSpec();
  const grids = [spec.layout!.grid, ...(spec.layout!.modes ?? []).map((m) => m.grid ?? spec.layout!.grid)];
  assert.ok(grids.length >= 4);
  for (const grid of grids) {
    const holds = gridGroups(grid!).some((g) => g.panels.includes(CORE_PANEL_IDS.storage));
    assert.ok(holds, "a group holds Storage");
  }
});

test("no source file holds mis-encoded text", () => {
  const bad = [String.fromCharCode(0xc3), String.fromCharCode(0xe2) + String.fromCharCode(0x20ac)];
  const root = new URL("../../", import.meta.url);
  const found: string[] = [];
  const walk = (dir: URL) => {
    for (const name of readdirSync(dir)) {
      const u = new URL(name + (statSync(new URL(name, dir)).isDirectory() ? "/" : ""), dir);
      if (statSync(u).isDirectory()) walk(u);
      else if (/\.(ts|tsx)$/.test(name) && bad.some((b) => readFileSync(u, "utf8").includes(b))) found.push(u.pathname);
    }
  };
  walk(root);
  assert.deepEqual(found.filter((f) => !f.endsWith("desktopRail.test.ts")), []);
});