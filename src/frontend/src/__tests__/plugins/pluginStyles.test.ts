// Enforces that the plugin overlay reaches the CSS pipeline.
//
// A UI shell replaces the whole viewer UI, and every one of them is styled with
// Tailwind. Three wiring facts have to hold or an overlaid UI renders as unstyled
// HTML — and each of the three failed silently, with a successful build and no
// warning anywhere:
//
//   1. `app.css` must be imported by the ENTRY (index.tsx). It used to be
//      imported by app.tsx, which became the built-in UI SHELL and is therefore
//      lazy-loaded — so an image whose default shell is an overlaid UI never
//      evaluated it. Tailwind's preflight, every utility class and the
//      html/body/#root sizing rules went with it.
//   2. Tailwind's `content` globs must cover `packages/plugins/**`. The JIT only
//      emits classes it can see; a class name that appears only in a plugin
//      package was silently dropped from the stylesheet.
//   3. `app.css` must import the generated plugin stylesheet entry, which is how
//      a plugin's own tokens and `@theme` registrations join the one Tailwind
//      build (the only place they can take effect).
//
// Static assertions on purpose: the alternative is a full vite build inside the
// unit suite, and what actually regresses here is a one-line edit to one of three
// files — which is exactly what reading them catches.

import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { test } from "node:test";

const here = dirname(fileURLToPath(import.meta.url));
const frontendRoot = resolve(here, "../../..");
const read = (rel: string) => readFileSync(join(frontendRoot, rel), "utf8");

test("the stylesheet is imported by the entry, not by the built-in shell", () => {
  const entry = read("src/index.tsx");
  assert.match(
    entry,
    /import\s+["']\.\/app\.css["']/,
    "src/index.tsx must import ./app.css — it is the only module every UI shell shares",
  );

  const app = read("src/app.tsx");
  assert.doesNotMatch(
    app,
    /import\s+["']\.\/app\.css["']/,
    "src/app.tsx is the built-in SHELL and is lazy-loaded; a CSS import here reaches " +
      "only builds where it is the active shell",
  );
});

test("tailwind scans plugin packages for class names", () => {
  const config = read("tailwind.config.js");
  const content = /content:\s*\[([^\]]*)\]/s.exec(config)?.[1] ?? "";
  assert.match(
    content,
    /packages\/plugins/,
    "tailwind's content globs must cover packages/plugins/** or a plugin's classes " +
      "are never generated",
  );
});

test("app.css pulls in the generated plugin stylesheet entry", () => {
  const css = read("src/app.css");
  const generated = "src/plugins/registry.generated.css";

  assert.ok(
    existsSync(join(frontendRoot, generated)),
    `${generated} is committed (empty in a stock build) so a plain vite build needs no pre-step`,
  );
  assert.match(
    css,
    /@import\s+["']\.\/plugins\/registry\.generated\.css["']/,
    "app.css must import the generated plugin stylesheet entry",
  );

  // CSS requires every @import to precede all other rules. Placing this one after
  // `@config` silently drops the whole file from the build — no error, no styles.
  const importIdx = css.indexOf("@import './plugins/registry.generated.css'");
  const configIdx = css.indexOf("@config");
  assert.ok(importIdx >= 0 && configIdx >= 0, "expected both directives in app.css");
  assert.ok(
    importIdx < configIdx,
    "the generated @import must come before @config — a late @import is dropped silently",
  );
});
