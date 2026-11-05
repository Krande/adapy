// Options on core's panel bodies that let a shell use them instead of copies:
// Options' sections, Simulation without its built-in tabs.

import assert from "node:assert/strict";
import { test } from "node:test";

await import("../fixtures/panelRealm");

const React = (await import("react")).default;
const { renderToStaticMarkup } = await import("react-dom/server");
const { OptionsBody } = await import("@/components/OptionsComponent");
const { SimulationBody } = await import("@/components/simulation/SimulationControls");
const { AdaViewerProvider } = await import("@/state/AdaViewerContext");

const inViewer = (el: React.ReactElement) => renderToStaticMarkup(React.createElement(AdaViewerProvider, null, el));

test("Options: a shell picks and orders its sections; by default all, in the classic order", () => {
  const all = renderToStaticMarkup(React.createElement(OptionsBody));
  for (const t of ["Scene config", "Theme", "Performance", "3D mouse", "Shortcut Keys"]) assert.match(all, new RegExp(t));
  assert.ok(all.indexOf("Scene config") < all.indexOf("Theme") && all.indexOf("Theme") < all.indexOf("Performance"));
  const some = renderToStaticMarkup(React.createElement(OptionsBody, { sections: ["performance", "theme"] }));
  assert.doesNotMatch(some, /Scene config|3D mouse|Shortcut Keys|Build:/);
  assert.ok(some.indexOf("Performance") < some.indexOf("Theme"), "in the order given");
  const hidden = renderToStaticMarkup(React.createElement(OptionsBody, { hiddenSections: ["build", "shortcuts"] }));
  assert.doesNotMatch(hidden, /Build:|Shortcut Keys/);
  assert.match(hidden, /Theme/);
});

test("Simulation without its built-in tabs: plugin tabs only, and a line when there are none", () => {
  const plain = inViewer(React.createElement(SimulationBody));
  assert.match(plain, /No results loaded/);
  const bare = inViewer(React.createElement(SimulationBody, { builtinTabs: false }));
  assert.match(bare, /No simulation tools for this model/);
  assert.doesNotMatch(bare, /No results loaded|role="tablist"/);
});
