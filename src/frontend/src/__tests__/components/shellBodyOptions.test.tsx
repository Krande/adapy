// Options on core's panel bodies that let a shell use them instead of copies:
// Options' sections, Simulation without its built-in tabs.

import assert from "node:assert/strict";
import { test } from "node:test";

await import("../fixtures/panelRealm");

const React = (await import("react")).default;
const { renderToStaticMarkup } = await import("react-dom/server");
const { OptionsBody } = await import("@/components/OptionsComponent");
const { SettingsBody } = await import("@/components/settings/SettingsPanel");
const { SimulationBody } = await import("@/components/simulation/SimulationControls");
const { AdaViewerProvider } = await import("@/state/AdaViewerContext");

const inViewer = (el: React.ReactElement) => renderToStaticMarkup(React.createElement(AdaViewerProvider, null, el));

test("Options: by default the Settings button and the shortcut keys, no settings sections", () => {
  const plain = renderToStaticMarkup(React.createElement(OptionsBody));
  assert.match(plain, />Settings</);
  assert.match(plain, /Shortcut Keys/);
  assert.ok(plain.indexOf(">Settings<") < plain.indexOf("Shortcut Keys"), "Settings above the shortcut keys");
  // The settings themselves are the Settings window's; the build line moved there too.
  assert.doesNotMatch(plain, /Scene config|Performance|3D mouse|Camera controls|Build:/);
});

test("Options: a shell picks and orders its sections; settings sections stay collapsible here", () => {
  const all = renderToStaticMarkup(
    React.createElement(OptionsBody, { sections: ["build", "scene", "theme", "performance", "spacemouse", "shortcuts"] }),
  );
  for (const t of ["Build:", "Scene config", "Theme", "Performance", "3D mouse", "Shortcut Keys"]) assert.match(all, new RegExp(t));
  assert.ok(all.indexOf("Scene config") < all.indexOf("Theme") && all.indexOf("Theme") < all.indexOf("Performance"));
  const some = renderToStaticMarkup(React.createElement(OptionsBody, { sections: ["performance", "theme"] }));
  assert.doesNotMatch(some, /Scene config|3D mouse|Shortcut Keys|Build:/);
  assert.ok(some.indexOf("Performance") < some.indexOf("Theme"), "in the order given");
  const hidden = renderToStaticMarkup(
    React.createElement(OptionsBody, { sections: ["build", "theme", "shortcuts"], hiddenSections: ["build", "shortcuts"] }),
  );
  assert.doesNotMatch(hidden, /Build:|Shortcut Keys/);
  assert.match(hidden, /Theme/);
});

test("Settings: one tab per available section, in the classic order, the first open, the build line below", () => {
  const html = renderToStaticMarkup(React.createElement(SettingsBody));
  const tabs = [...html.matchAll(/role="tab"[^>]*>([^<]+)</g)].map((m) => m[1]);
  // The REST-only sections (Session restore, Conversion engine) are left out outside REST mode.
  assert.deepEqual(tabs, ["Scene config", "Appearance", "Theme", "Performance", "Calculations", "Camera controls", "3D mouse"]);
  assert.match(html, /aria-selected="true"[^>]*>Scene config</);
  assert.match(html, /role="tabpanel" aria-label="Scene config"/);
  assert.match(html, /Build:/);
  const camera = renderToStaticMarkup(React.createElement(SettingsBody, { initialTab: "camera" }));
  assert.match(camera, /aria-selected="true"[^>]*>Camera controls</);
});

test("Simulation without its built-in tabs: plugin tabs only, and a line when there are none", () => {
  const plain = inViewer(React.createElement(SimulationBody));
  assert.match(plain, /No results loaded/);
  const bare = inViewer(React.createElement(SimulationBody, { builtinTabs: false }));
  assert.match(bare, /No simulation tools for this model/);
  assert.doesNotMatch(bare, /No results loaded|role="tablist"/);
});
