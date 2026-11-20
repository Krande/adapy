// The Clashes tab's run form sits under an "Input" header that folds it away: the shared
// disclosure (`CollapsibleSection`, the Inspect tab's "Inputs" / "Members" / "Welds"), open
// when the tab is opened.

import assert from "node:assert/strict";
import { test } from "node:test";

const { setServerState } = await import("../fixtures/panelRealm");

const React = (await import("react")).default;
const { renderToStaticMarkup } = await import("react-dom/server");
const { AdaViewerProvider } = await import("@/state/AdaViewerContext");
const { default: ClashesPanel } = await import("@/components/info_box_scene/ClashesPanel");
const { useModelState } = await import("@/state/modelState");

const panel = () => renderToStaticMarkup(React.createElement(AdaViewerProvider, null, React.createElement(ClashesPanel)));

test("the run form is under an open, collapsible Input header", () => {
  setServerState(useModelState, { loadedSourceName: "frame.ifc" } as never);
  const html = panel();
  const header = /<button type="button" class="[^"]*font-bold[^"]*" aria-expanded="true"><svg[^]*?<\/svg><span>Input<\/span><\/button>/.exec(html);
  assert.ok(header, "an Input disclosure header, open");
  const run = html.indexOf("Run clash check");
  assert.ok(run > header!.index, "the run form is under it");
  assert.ok(html.indexOf("Checking:") > header!.index, "the check target line too");
});

test("with nothing loaded there is no form and no Input header", () => {
  setServerState(useModelState, { loadedSourceName: null } as never);
  const html = panel();
  assert.doesNotMatch(html, /<span>Input<\/span>/);
  assert.match(html, /No model loaded\./);
});
