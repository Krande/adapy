// A placed plugin panel re-asks its activation predicate when the loaded models change
// or the plugin says its own state did: a place restored before its model or results
// exist (a mode reopened on a cold load) must not stay "Nothing to show here yet".

import assert from "node:assert/strict";
import { test } from "node:test";

const dom = await import("../fixtures/miniDom");
await import("../fixtures/panelRealm");

const React = (await import("react")).default;
const { act } = await import("react");
const { createRoot } = await import("react-dom/client");
const R = await import("@/plugins/registry");
const { makePluginPanelContent } = await import("@/layout/PluginPanelBody");
const { AdaViewerProvider } = await import("@/state/AdaViewerContext");
const V = await import("@/viewer-core");

test("notifyPluginActivationChanged tells its listeners and moves the version", () => {
  let told = 0;
  const off = R.subscribePluginActivation(() => told++);
  const before = R.pluginActivationVersion();
  V.notifyPluginActivationChanged();
  off();
  V.notifyPluginActivationChanged();
  assert.equal(told, 1);
  assert.equal(R.pluginActivationVersion(), before + 2);
});

test("a placed panel shows itself once its predicate holds, without a remount", async () => {
  let relevant = false;
  const Content = makePluginPanelContent("p", {
    id: "p:panel",
    region: "left-sidebar",
    activationPredicate: () => relevant,
    render: () => React.createElement("span", null, "the panel"),
  } as never);
  const el = dom.container();
  const root = createRoot(el as unknown as Element);
  await act(async () => root.render(React.createElement(AdaViewerProvider, null, React.createElement(Content, {} as never))));
  assert.match(el.textContent ?? "", /Nothing to show here yet/);
  relevant = true;
  await act(async () => V.notifyPluginActivationChanged());
  assert.match(el.textContent ?? "", /the panel/);
  await act(async () => root.unmount());
});
