// The Robotic Simulations panel's runs list folds under its heading and starts folded: the heading
// says how many there are, and the list is drawn once the heading is opened.
import assert from "node:assert/strict";
import { test } from "node:test";

const { setServerState } = await import("../fixtures/panelRealm");

const React = (await import("react")).default;
const { renderToStaticMarkup } = await import("react-dom/server");
const { AdaViewerProvider } = await import("@/state/AdaViewerContext");
const { SimulationsBody } = await import("@/components/simulations/SimulationsPanel");
const { useSimulationStore } = await import("@/state/simulationStore");

const run = (id: string) =>
  ({
    run_id: id,
    status: "done",
    provider: { id: "p", domain: "d", title: "P" },
    parameters: {},
    artefacts: [],
    summary: {},
    created_at: "2026-11-18T08:00:00Z",
  }) as never;

test("the runs are folded under their heading, which says how many there are", () => {
  setServerState(useSimulationStore, {
    runs: [run("a"), run("b")],
    loadProviders: async () => {},
    loadRuns: async () => {},
  } as never);
  const html = renderToStaticMarkup(React.createElement(AdaViewerProvider, null, React.createElement(SimulationsBody)));
  assert.match(html, /aria-expanded="false"[^>]*data-runs-toggle/);
  assert.match(html, />Runs \(2\)</);
  assert.doesNotMatch(html, /data-runs-list/, "the list is not drawn while folded");
  assert.doesNotMatch(html, /No runs in this scope yet/);
  // Refresh stays in the heading while folded.
  assert.match(html, /data-runs-refresh/);
});
