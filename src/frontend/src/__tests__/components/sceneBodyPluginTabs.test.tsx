// A plugin's tabs of the Scene panel (plugin API 1.31.0): a `scene-info` panel with `asTab` is a
// tab of the Scene panel's strip, after core's groups, ordered by `asTab.order`; only the tab on
// show is mounted, and opening one of core's tabs leaves it. One without `asTab` is not a tab.

import assert from "node:assert/strict";
import { beforeEach, test } from "node:test";

const { setServerState } = await import("../fixtures/panelRealm");

const React = (await import("react")).default;
const { renderToStaticMarkup } = await import("react-dom/server");
const { AdaViewerProvider } = await import("@/state/AdaViewerContext");
const { default: SceneBody } = await import("@/components/info_box_scene/SceneBody");
const { useSceneInfoStore } = await import("@/state/sceneInfoStore");
const { getPanel, resetPanelRegistry } = await import("@/layout/registry");
const { getRegisteredPlugins, getSceneTabs, registerPlugin, resetRegistry, PLUGIN_API_VERSION } = await import(
  "@/plugins/registry"
);

type Props = import("@/components/info_box_scene/SceneBody").SceneBodyProps;
const render = (props?: Props) =>
  renderToStaticMarkup(React.createElement(AdaViewerProvider, null, React.createElement(SceneBody, props)));
const tabLabels = (html: string) => [...html.matchAll(/role="tab"[^>]*>(?:<span[^>]*><\/span>)?([^<]+)</g)].map((m) => m[1]);

beforeEach(() => {
  resetRegistry();
  resetPanelRegistry();
  setServerState(useSceneInfoStore, { mode: "info", pluginTab: null });
});

const registerDemo = () =>
  registerPlugin({
    id: "demo",
    panels: [
      { id: "late", region: "scene-info", asTab: { label: "Late", order: 20, badge: () => 2 }, render: () => "the late tab" },
      { id: "early", region: "scene-info", asTab: { label: "Early", order: 10 }, render: () => "the early tab" },
      { id: "sec", region: "scene-info", render: () => "a section, not a tab" },
      { id: "gated", region: "scene-info", asTab: { label: "Gated" }, activationPredicate: () => false, render: () => "gated" },
      { id: "cb", region: "connection-builder", asTab: { label: "Elsewhere" }, render: () => "elsewhere" },
    ],
  });

test("the API version that has them", () => {
  assert.equal(PLUGIN_API_VERSION, "1.31.0");
});

test("a scene-info panel with asTab is placed as a tab of the Scene panel; one without, a section", () => {
  registerDemo();
  assert.deepEqual(getPanel("demo:late")?.placement?.classic, {
    zone: "classic.menu-column",
    parent: "core:scene",
    as: "tab",
    order: 20,
  });
  assert.equal(getPanel("demo:sec")?.placement?.classic?.as, "section");
  assert.deepEqual(
    getSceneTabs({} as never).map((t) => [t.panel.id, t.asTab.label]),
    [["demo:early", "Early"], ["demo:late", "Late"]],
  );
});

test("the strip: core's groups, then the plugin's tabs in order; core's tab on show", () => {
  const plain = render();
  registerDemo();
  const html = render();
  assert.deepEqual(tabLabels(html), ["Model", "View", "Measure", "Tools", "Early", "Late"]);
  assert.match(html, /aria-selected="true"[^>]*>Model</);
  assert.match(html, />2<\/span>/, "the tab's badge");
  assert.doesNotMatch(html, /the early tab|the late tab|a section, not a tab|gated|elsewhere/);
  // Without a plugin tab, the panel is as it was.
  resetRegistry();
  assert.equal(render(), plain);
});

test("a picked plugin tab is the one mounted; core's groups are not selected and have no second strip", () => {
  registerDemo();
  setServerState(useSceneInfoStore, { mode: "section", pluginTab: "demo:late" });
  const html = render();
  assert.match(html, /aria-selected="true"[^>]*>Late/);
  assert.doesNotMatch(html, /aria-selected="true"[^>]*>(View|Model|Clip)</);
  assert.doesNotMatch(html, /aria-label="View section"/);
  assert.match(html, /the late tab/);
  assert.doesNotMatch(html, /the early tab|Snap to corners/);
});

test("opening one of core's tabs leaves the plugin's tab", () => {
  useSceneInfoStore.getState().setPluginTab("demo:early");
  assert.equal(useSceneInfoStore.getState().pluginTab, "demo:early");
  useSceneInfoStore.getState().setMode("measure");
  assert.equal(useSceneInfoStore.getState().pluginTab, null);
});

test("a picked tab that went away leaves core's on show; a throwing badge disables its plugin only", () => {
  registerPlugin({
    id: "bad",
    panels: [{ id: "t", region: "scene-info", asTab: { label: "Bad", badge: () => { throw new Error("no"); } }, render: () => "bad" }],
  });
  setServerState(useSceneInfoStore, { pluginTab: "gone:tab" });
  const html = render();
  assert.match(html, /aria-selected="true"[^>]*>Model</);
  assert.match(String(getRegisteredPlugins().find((p) => p.id === "bad")?.disabled), /tab badge threw/);
});

test("stacked: each plugin tab is a section after core's, the picked one open", () => {
  registerDemo();
  setServerState(useSceneInfoStore, { pluginTab: "demo:early" });
  const html = render({ arrangement: "stacked" });
  const expanded = (label: string) =>
    new RegExp(`aria-expanded="(true|false)">(?:(?!<button).)*?<span>${label}</span>`).exec(html)?.[1];
  assert.equal(expanded("Early"), "true");
  assert.equal(expanded("Late"), "false");
  assert.equal(expanded("Model"), "false", "core's tab is not the one on show");
  assert.match(html, /<span>Early<\/span><\/button><div class="pb-1">the early tab/);
  assert.doesNotMatch(html, /the late tab/);
  assert.ok(html.indexOf("<span>Tools</span>") < html.indexOf("<span>Early</span>"));
});
