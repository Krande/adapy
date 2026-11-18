// The Review tab's plugin sections (region `joint-review`, plugin API 1.29.0): a plugin's section
// renders after core's own (the produced and the identified joints), in `order`, headed by its
// title; with none the tab is what it was; and an active section makes the Review tab be there even
// when core has no joints to show.

import assert from "node:assert/strict";
import { beforeEach, test } from "node:test";

const { setServerState } = await import("../fixtures/panelRealm");

const React = (await import("react")).default;
const { renderToStaticMarkup } = await import("react-dom/server");
const { AdaViewerProvider } = await import("@/state/AdaViewerContext");
const { ConnectionBuilderPanelContent } = await import("@/components/component_view/ConnectionBuilderPanel");
const { default: JointsOverviewPanel } = await import("@/components/info_box_scene/JointsOverviewPanel");
const { JOINTS_REVIEW_TAB, useConnectionBuilderTabStore } = await import("@/state/connectionBuilderTabStore");
const { resetPanelRegistry, getPanel } = await import("@/layout/registry");
const { useConnectionsOfferStore } = await import("@/state/connectionsOfferStore");
const { useStatsStore } = await import("@/state/statsStore");
const { getJointReviewSections, getRegisteredPlugins, registerPlugin, resetRegistry } = await import("@/plugins/registry");

const within = (node: React.ReactElement) => renderToStaticMarkup(React.createElement(AdaViewerProvider, null, node));
const review = () => within(React.createElement(JointsOverviewPanel));
const panel = () => within(React.createElement(ConnectionBuilderPanelContent));
const strip = (html: string) =>
  [...html.matchAll(/role="tab" aria-selected="(true|false)"[^>]*>(?:<span[^>]*><\/span>)?([^<]+)/g)].map((m) => m[2]);
/** The plugin sections in the order they are rendered, as [id, title]. */
const sections = (html: string) =>
  [...html.matchAll(/<section[^>]*data-review-section="([^"]+)" aria-label="([^"]+)"/g)].map((m) => [m[1], m[2]]);

const PRODUCED = {
  joints: {
    count: 1,
    by_type: [{ slug: "t", name: "Type T", count: 1 }],
    items: [{ name: "J1", slug: "t", type: "t", members: ["A", "B"], plates: 0, welds: 2, centre: null }],
  },
};

beforeEach(() => {
  resetRegistry();
  resetPanelRegistry();
  setServerState(useConnectionBuilderTabStore, { tab: JOINTS_REVIEW_TAB });
  setServerState(useConnectionsOfferStore, { offered: false });
  setServerState(useStatsStore, { stats: null });
});

test("no plugin section: the Review tab shows core's sections only, and is not there without joints", () => {
  assert.deepEqual(getJointReviewSections({} as never), []);
  assert.ok(!strip(panel()).includes("Review"), "no joints and no section: no Review tab");
  setServerState(useStatsStore, { stats: PRODUCED as never });
  const html = review();
  assert.match(html, /produced joint/);
  assert.deepEqual(sections(html), []);
  assert.doesNotMatch(html, /<section/);
});

test("a plugin's sections come after core's, in order, each headed by its title", () => {
  setServerState(useStatsStore, { stats: PRODUCED as never });
  registerPlugin({
    id: "demo",
    panels: [
      { id: "late", region: "joint-review", title: "Later", order: 5, render: () => "the later section" },
      { id: "early", region: "joint-review", title: "Earlier", order: 1, render: () => "the earlier section" },
      { id: "gated", region: "joint-review", title: "Gated", activationPredicate: () => false, render: () => "gated" },
      { id: "tab", region: "connection-builder", asTab: { label: "A tab" }, render: () => "a tab, not a section" },
    ],
  });
  const html = review();
  assert.deepEqual(sections(html), [["demo:early", "Earlier"], ["demo:late", "Later"]]);
  const produced = html.indexOf("produced joint");
  assert.ok(produced >= 0 && produced < html.indexOf("the earlier section"), "core's produced joints come first");
  assert.ok(html.indexOf("the earlier section") < html.indexOf("the later section"));
  assert.doesNotMatch(html, /gated|a tab, not a section/);
  assert.deepEqual(
    getPanel("demo:late")?.placement?.classic,
    { zone: "classic.menu-column", parent: "core:component-controls", as: "section", order: 5 },
    "placed as a section of the Connections panel",
  );
});

test("an active section makes the Review tab, joints or not; it goes with the section", () => {
  let active = true;
  registerPlugin({
    id: "demo",
    panels: [{ id: "s", region: "joint-review", title: "Mine", activationPredicate: () => active, render: () => "my section" }],
  });
  let html = panel();
  assert.ok(strip(html).includes("Review"), "the section makes the tab");
  assert.match(html, /aria-selected="true"[^>]*>(?:<span[^>]*><\/span>)?Review/);
  assert.match(html, /my section/);
  assert.doesNotMatch(html, /No joints yet/, "no empty state while a section has something to show");
  active = false;
  html = panel();
  assert.ok(!strip(html).includes("Review"), "the section gone, the tab gone");
});

test("a section can send the user to a plugin's tab: openConnectionsTab picks it", async () => {
  // What `@/viewer-core/app` re-exports (that entry point mounts a viewer, so not imported here).
  const V = await import("@/layout/connectionsTabActions");
  registerPlugin({
    id: "demo",
    panels: [{ id: "tab", region: "connection-builder", asTab: { label: "Mine" }, render: () => "my tab" }],
  });
  V.openConnectionsTab("demo:tab");
  assert.equal(useConnectionBuilderTabStore.getState().tab, "demo:tab");
});

test("a disabled plugin's section is not shown", async () => {
  const { disablePlugin } = await import("@/plugins/registry");
  setServerState(useStatsStore, { stats: PRODUCED as never });
  registerPlugin({ id: "bad", panels: [{ id: "s", region: "joint-review", title: "Bad", render: () => "bad section" }] });
  registerPlugin({ id: "good", panels: [{ id: "s", region: "joint-review", title: "Good", render: () => "good section" }] });
  disablePlugin("bad", "test");
  const html = review();
  assert.match(html, /good section/);
  assert.doesNotMatch(html, /bad section/);
  assert.ok(getRegisteredPlugins().find((p) => p.id === "bad")?.disabled);
});
