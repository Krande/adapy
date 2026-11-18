// The Connections panel's tabs: core's first -- Inspect (where the builder is offered), Clashes,
// Overlays, Review (while there are joints) -- then a plugin's tabs; only the tab on show is
// mounted. Clashes, Overlays and Review were the Scene panel's Joints group.

import assert from "node:assert/strict";
import { beforeEach, test } from "node:test";

const { setServerState } = await import("../fixtures/panelRealm");

const React = (await import("react")).default;
const { renderToStaticMarkup } = await import("react-dom/server");
const { AdaViewerProvider } = await import("@/state/AdaViewerContext");
const { ConnectionBuilderPanelContent, BUILDER_TAB_LABEL } = await import("@/components/component_view/ConnectionBuilderPanel");
const { ConnectionBuilderBody } = await import("@/components/component_view/ConnectionBuilder");
const { default: ClashesPanel } = await import("@/components/info_box_scene/ClashesPanel");
const { default: JointOverlaysPanel } = await import("@/components/info_box_scene/JointOverlaysPanel");
const { BUILDER_TAB, CLASHES_TAB, JOINT_OVERLAYS_TAB, JOINTS_REVIEW_TAB, useConnectionBuilderTabStore } = await import(
  "@/state/connectionBuilderTabStore"
);
const { CONNECTIONS_CORE_TABS, connectionsActiveTab, connectionsCoreTabs } = await import(
  "@/components/component_view/connectionsTabs"
);
const { resetPanelRegistry } = await import("@/layout/registry");
const { useConnectionsOfferStore } = await import("@/state/connectionsOfferStore");
const { useStatsStore } = await import("@/state/statsStore");
const { hasConnectionBuilderTabs } = await import("@/plugins/registry");
const { getRegisteredPlugins, registerPlugin, resetRegistry } = await import("@/plugins/registry");

const within = (node: React.ReactElement) =>
  renderToStaticMarkup(React.createElement(AdaViewerProvider, null, node));
const panel = () => within(React.createElement(ConnectionBuilderPanelContent));
/** The strip's tabs, as [label, selected]. */
const strip = (html: string) =>
  [...html.matchAll(/role="tab" aria-selected="(true|false)"[^>]*>(?:<span[^>]*><\/span>)?([^<]+)/g)].map((m) => [m[2], m[1]]);
/** The tab on show is the panel's last child. */
const shows = (html: string, node: React.ReactElement) => html.endsWith(within(node) + "</div>");

beforeEach(() => {
  resetRegistry();
  resetPanelRegistry();
  setServerState(useConnectionBuilderTabStore, { tab: BUILDER_TAB });
  // The scope offers specs: the builder is there.
  setServerState(useConnectionsOfferStore, { offered: true });
  setServerState(useStatsStore, { stats: null });
});

test("core's tabs: Inspect, Clashes, Overlays, and Review only while there are joints", () => {
  assert.deepEqual(
    CONNECTIONS_CORE_TABS.map((t) => [t.id, t.label, !!t.ctx]),
    [
      [BUILDER_TAB, "Inspect", false],
      [CLASHES_TAB, "Clashes", false],
      [JOINT_OVERLAYS_TAB, "Overlays", false],
      [JOINTS_REVIEW_TAB, "Review", true],
    ],
  );
  assert.equal(BUILDER_TAB_LABEL, "Inspect");
  const ids = (builder: boolean, joints: boolean) => connectionsCoreTabs({ builder, joints }).map((t) => t.id);
  assert.deepEqual(ids(true, false), [BUILDER_TAB, CLASHES_TAB, JOINT_OVERLAYS_TAB]);
  assert.deepEqual(ids(true, true), [BUILDER_TAB, CLASHES_TAB, JOINT_OVERLAYS_TAB, JOINTS_REVIEW_TAB]);
  assert.deepEqual(ids(false, false), [CLASHES_TAB, JOINT_OVERLAYS_TAB], "never empty: Clashes and Overlays always");
  assert.deepEqual(ids(false, true), [CLASHES_TAB, JOINT_OVERLAYS_TAB, JOINTS_REVIEW_TAB]);
  assert.equal(connectionsActiveTab(JOINTS_REVIEW_TAB, ids(true, false)), BUILDER_TAB, "a tab gone falls back to the first");
  assert.equal(connectionsActiveTab(JOINTS_REVIEW_TAB, ids(false, false)), CLASHES_TAB);
  assert.equal(connectionsActiveTab(CLASHES_TAB, ids(true, false)), CLASHES_TAB);
});

test("no plugin tab: a strip of core's tabs, the builder on show", () => {
  const html = panel();
  assert.match(html, /role="tablist" aria-label="Connections section"/);
  assert.deepEqual(strip(html), [["Inspect", "true"], ["Clashes", "false"], ["Overlays", "false"]]);
  assert.ok(shows(html, React.createElement(ConnectionBuilderBody)), "the builder is on show");
  // A panel of another region is not a tab here.
  registerPlugin({ id: "other", panels: [{ id: "p", region: "top-panel", render: () => "elsewhere" }] });
  assert.equal(panel(), html);
});

test("Review is there while the loaded model's take-off has joints, marked contextual", () => {
  setServerState(useStatsStore, { stats: { joints: { count: 2 } } as never });
  try {
    const html = panel();
    assert.deepEqual(strip(html), [["Inspect", "true"], ["Clashes", "false"], ["Overlays", "false"], ["Review", "false"]]);
    assert.match(html, /<span class="w-1\.5 h-1\.5 rounded-full bg-violet-400" aria-hidden="true"><\/span>Review/);
  } finally {
    setServerState(useStatsStore, { stats: null });
  }
});

test("Clashes and Overlays show their own content when picked", () => {
  setServerState(useConnectionBuilderTabStore, { tab: CLASHES_TAB });
  let html = panel();
  assert.match(html, /aria-selected="true"[^>]*>Clashes/);
  assert.ok(shows(html, React.createElement(ClashesPanel)), "Clashes is on show");
  setServerState(useConnectionBuilderTabStore, { tab: JOINT_OVERLAYS_TAB });
  html = panel();
  assert.match(html, /aria-selected="true"[^>]*>Overlays/);
  assert.ok(shows(html, React.createElement(JointOverlaysPanel)), "Overlays is on show");
});

test("a plugin's tabs come after core's, and the tab on show is the one mounted", () => {
  registerPlugin({
    id: "demo",
    panels: [
      { id: "extra", region: "connection-builder", asTab: { label: "Extra", badge: () => 3 }, render: () => "the extra tab" },
      { id: "first", region: "connection-builder", asTab: { label: "First", order: -1 }, render: () => "the first tab" },
    ],
  });
  let html = panel();
  assert.deepEqual(strip(html), [
    ["Inspect", "true"],
    ["Clashes", "false"],
    ["Overlays", "false"],
    ["First", "false"],
    ["Extra", "false"],
  ]);
  assert.match(html, />3<\/span>/, "the tab's badge");
  assert.ok(shows(html, React.createElement(ConnectionBuilderBody)), "the builder is on show");
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
  let html = panel();
  assert.match(html, /aria-selected="true"[^>]*>Inspect/);
  assert.match(html, /Connecting/);
  assert.match(String(getRegisteredPlugins().find((p) => p.id === "bad")?.disabled), /tab badge threw/);
  assert.ok(!getRegisteredPlugins().find((p) => p.id === "gone")?.disabled);
  // Review picked with no joints left: the builder too.
  setServerState(useConnectionBuilderTabStore, { tab: JOINTS_REVIEW_TAB });
  html = panel();
  assert.match(html, /aria-selected="true"[^>]*>Inspect/);
});

test("where the builder is not offered its tab is left out: Clashes first, then the plugins' tabs", () => {
  setServerState(useConnectionsOfferStore, { offered: false });
  assert.equal(hasConnectionBuilderTabs(), false);
  let html = panel();
  assert.deepEqual(strip(html), [["Clashes", "true"], ["Overlays", "false"]]);
  assert.ok(shows(html, React.createElement(ClashesPanel)), "the picked builder is not there: Clashes is on show");
  registerPlugin({
    id: "demo",
    panels: [
      { id: "b", region: "connection-builder", asTab: { label: "Second", order: 2 }, render: () => "the second tab" },
      { id: "a", region: "connection-builder", asTab: { label: "First", order: 1 }, render: () => "the first tab" },
    ],
  });
  assert.equal(hasConnectionBuilderTabs(), true);
  html = panel();
  assert.deepEqual(strip(html), [["Clashes", "true"], ["Overlays", "false"], ["First", "false"], ["Second", "false"]]);
  assert.doesNotMatch(html, /Inspect|Connecting|the first tab|the second tab/);
  setServerState(useConnectionBuilderTabStore, { tab: "demo:b" });
  html = panel();
  assert.match(html, /aria-selected="true"[^>]*>Second/);
  assert.match(html, /the second tab/);
});
