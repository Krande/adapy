import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

// The guard that keeps the stores and the scene layer on the capability seam:
// a runtime import of `services/viewerApi` under the guarded directories fails
// the check; type-only imports of its DTOs pass. Driven through the CLI on a
// fixture tree (`--root`) so the assertions do not depend on the real source
// (and the .mjs needs no TypeScript declaration), plus once against the real
// src/ so a regression fails here and not only in `pretest`.

const script = fileURLToPath(new URL("../../../scripts/check-capability-seam.mjs", import.meta.url));
const realSrc = fileURLToPath(new URL("../../", import.meta.url));

function run(root: string): { code: number; out: string } {
  try {
    const out = execFileSync(process.execPath, [script, "--root", root], { encoding: "utf8", stdio: "pipe" });
    return { code: 0, out };
  } catch (e) {
    const err = e as { status: number; stdout: string; stderr: string };
    return { code: err.status, out: `${err.stdout}${err.stderr}` };
  }
}

function withFixture(fn: (root: string) => void): void {
  const root = mkdtempSync(join(tmpdir(), "seam-"));
  try {
    mkdirSync(join(root, "state"), { recursive: true });
    mkdirSync(join(root, "utils", "scene", "handlers"), { recursive: true });
    mkdirSync(join(root, "components", "viewer", "sceneHelpers"), { recursive: true });
    fn(root);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
}

test("type-only forms and commented-out imports pass", () => {
  withFixture((root) => {
    writeFileSync(
      join(root, "state", "good.ts"),
      [
        'import type { FeaManifest } from "@/services/viewerApi";',
        'import { type ScopeUrl, type ConvertResponse } from "@/services/viewerApi";',
        'type Api = typeof import("@/services/viewerApi");',
        '// import { viewerApi } from "@/services/viewerApi"; -- commented out',
        '/* import { viewerApi } from "@/services/viewerApi"; */',
        'import { capabilities } from "@/services/capabilities";',
        "",
      ].join("\n"),
    );
    writeFileSync(
      join(root, "utils", "scene", "handlers", "fine.ts"),
      'import { capabilities } from "@/services/capabilities";\n',
    );
    const r = run(root);
    assert.equal(r.code, 0, r.out);
    assert.match(r.out, /capability seam OK: 2 files/);
  });
});

test("every runtime form is reported with its file and line", () => {
  withFixture((root) => {
    writeFileSync(
      join(root, "state", "bad.ts"),
      [
        'import { viewerApi } from "@/services/viewerApi";', // 1
        'import { type ScopeUrl, viewerApi as api } from "../../services/viewerApi";', // 2
        'import * as everything from "@/services/viewerApi";', // 3
        'const { viewerApi } = await import("@/services/viewerApi");', // 4
        'export { ApiError } from "@/services/viewerApi";', // 5
        'import { TargetFormat } from "@/services/viewerApi";', // 6: a DTO without the modifier still fails
        "",
      ].join("\n"),
    );
    writeFileSync(
      join(root, "components", "viewer", "sceneHelpers", "loader.ts"),
      'async function f() {\n  const {viewerApi} = await import("@/services/viewerApi");\n}\n',
    );
    const r = run(root);
    assert.equal(r.code, 1);
    for (const line of [1, 2, 3, 4, 5, 6]) {
      assert.match(r.out, new RegExp(`state/bad\\.ts:${line}: `), `line ${line}\n${r.out}`);
    }
    assert.match(r.out, /state\/bad\.ts:4: dynamic import\(\)/);
    assert.match(r.out, /state\/bad\.ts:6: runtime import of \{TargetFormat\}.*`type` modifier/);
    assert.match(r.out, /components\/viewer\/sceneHelpers\/loader\.ts:2: dynamic import\(\)/);
    assert.match(r.out, /7 runtime import\(s\)/);
  });
});

test("the real src/ tree is on the seam", () => {
  const r = run(realSrc);
  assert.equal(r.code, 0, r.out);
});
