// The plugin slot hosts must SUBSCRIBE to the theme, not snapshot it.
//
// `effectivePluginTheme(useThemeStore.getState())` reads the store without
// subscribing. Core's own chrome does not care: it paints from the `--ada-*`
// CSS variables, which the store writes to the document element, so the browser
// applies a theme switch immediately. A plugin panel handed the token OBJECT has
// no such luck — it keeps whatever values were current at its last render.
//
// The hosts subscribed to the viewer stores and to the plugin-visibility store,
// and never to the theme. So switching theme repainted core and left every
// plugin panel on the old palette until something unrelated re-rendered it.
//
// Asserted on the source rather than by mounting: the defect is "a subscription
// is missing", which is a property of the source. A behavioural version would
// need a DOM and a mounted host to restate what one read of the file settles.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "../../..");
const read = (rel: string) => readFileSync(resolve(root, rel), "utf8");

test("both plugin slot hosts subscribe to the theme store", () => {
  const src = read("src/plugins/PluginSlots.tsx");
  assert.ok(
    src.includes('import { usePluginTheme } from "@/state/themeStore";'),
    "PluginSlots must import usePluginTheme",
  );
  assert.equal(
    src.split("const theme = usePluginTheme();").length - 1,
    2,
    "both hosts must subscribe — the panel region and the top-bar buttons — " +
      "or one of them repaints late",
  );
});

test("every slot-host context carries the subscribed theme", () => {
  const src = read("src/plugins/PluginSlots.tsx");
  const calls = src.match(/makePluginContext\([^;]*?\)/g) ?? [];
  assert.ok(calls.length >= 4, `expected several makePluginContext calls, saw ${calls.length}`);
  for (const call of calls) {
    assert.ok(
      call.includes("theme"),
      `${call} drops the subscribed theme, so that panel keeps a stale palette`,
    );
  }
});

test("makePluginContext still works without a theme, for callers outside React", () => {
  const src = read("src/plugins/context.ts");
  assert.ok(
    src.includes("theme?: PluginTheme"),
    "the theme argument must stay optional — the sidecar-loader run-point and " +
      "url-param dispatch build contexts with no React tree to subscribe in",
  );
  assert.ok(
    src.includes("theme ?? effectivePluginTheme(useThemeStore.getState())"),
    "the snapshot must remain the fallback",
  );
});

test("usePluginTheme subscribes to exactly the fields the theme derives from", () => {
  const src = read("src/state/themeStore.ts");
  const start = src.indexOf("export function usePluginTheme");
  assert.ok(start >= 0, "usePluginTheme must exist");
  const hook = src.slice(start, src.indexOf("}", start));
  // Anything effectivePluginTheme reads must be subscribed, or a change to it
  // silently does not repaint; anything else would re-render on unrelated writes.
  for (const field of ["preset", "customBg", "customText", "bgOpacity"]) {
    assert.ok(hook.includes(`s.${field}`), `usePluginTheme must subscribe to ${field}`);
  }
});
