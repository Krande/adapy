// The connection builder panel's tabs: without a plugin's tab the panel is the builder alone, as it
// was; with one, a strip with the builder first, and only the tab on show is mounted.

import assert from "node:assert/strict";
import { beforeEach, test } from "node:test";

const { setServerState } = await import("../fixtures/panelRealm");

const React = (await import("react")).default;
const { renderToStaticMarkup } = await import("react-dom/server");
const { AdaViewerProvider } = await import("@/state/AdaViewerContext");
const { ConnectionBuilderPanelContent } = await import("@/components/component_view/ConnectionBuilderPanel");
const { ConnectionBuilderBody } = await import("@/components/component_view/ConnectionBuilder");
const { BUILDER_TAB, useConnectionBuilderTabStore } = await import("@/state/connectionBuilderTabStore");
const { resetPanelRegistry } = await import("@/layout/registry");
const { getRegisteredPlugins, registerPlugin, resetRegistry } = await import("@/plugins/registry");

const within = (node: React.ReactElement) =>
  renderToStaticMarkup(React.createElement(AdaViewerProvider, null, node));
const panel = () => within(React.createElement(ConnectionBuilderPanelContent));

beforeEach(() => {
  resetRegistry();
  resetPanelRegistry();
  setServerState(useConnectionBuilderTabStore, { tab: BUILDER_TAB });
});

test("no plugin tab: the panel is the builder alone, with no tab strip", () => {
  const html = panel();
  assert.equal(html, within(React.createElement(ConnectionBuilderBody)));
  assert.doesNotMatch(html, /role="tab/);
  // A panel of another region is not a tab here.
  registerPlugin({ id: "other", panels: [{ id: "p", region: "top-panel", render: () => "elsewhere" }] });
  assert.equal(panel(), html);
});

test("a plugin's tab: a strip with the builder first, and the tab on show is the one mounted", () => {
  registerPlugin({
    id: "demo",
    panels: [
      { id: "extra", region: "connection-builder", asTab: { label: "Extra", badge: () => 3 }, render: () => "the extra tab" },
      { id: "first", region: "connection-builder", asTab: { label: "First", order: -1 }, render: () => "the first tab" },
    ],
  });
  let html = panel();
  assert.match(html, /role="tablist" aria-label="Connection builder section"/);
  const labels = [...html.matchAll(/role="tab" aria-selected="(true|false)"[^>]*>(?:<span[^>]*><\/span>)?([^<]+)/g)].map((m) => [m[2], m[1]]);
  assert.deepEqual(labels, [["Builder", "true"], ["First", "false"], ["Extra", "false"]]);
  assert.match(html, />3<\/span>/, "the tab's badge");
  assert.ok(html.endsWith(within(React.createElement(ConnectionBuilderBody)) + "</div>"), "the builder is on show");
  assert.doesNotMatch(html, /the extra tab|the first tab/);

  setServerState(useConnectionBuilderTabStore, { tab: "demo:extra" });
  html = panel();
  assert.match(html, /aria-selected="true"[^>]*>Extra/);
  assert.match(html, /the extra tab/);
  assert.doesNotMatch(html, /the first tab|Connecting/, "the builder and the other tab are not mounted");
});

test("a picked tab that went away falls back to the builder; a throwing badge disables its plugin only", () => {
  registerPlugin({ id: "gone", panels: [{ id: "t", region: "connection-builder", asTab: { label: "Gone" }, render: () => "gone tab" }] });
  registerPlugin({
    id: "bad",
    panels: [{ id: "t", region: "connection-builder", asTab: { label: "Bad", badge: () => { throw new Error("no"); } }, render: () => "bad tab" }],
  });
  setServerState(useConnectionBuilderTabStore, { tab: "elsewhere:tab" });
  const html = panel();
  assert.match(html, /aria-selected="true"[^>]*>Builder/);
  assert.match(html, /Connecting/);
  assert.match(String(getRegisteredPlugins().find((p) => p.id === "bad")?.disabled), /tab badge threw/);
  assert.ok(!getRegisteredPlugins().find((p) => p.id === "gone")?.disabled);
});
