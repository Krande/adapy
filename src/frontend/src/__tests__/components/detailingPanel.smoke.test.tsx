import assert from "node:assert/strict";
import { test } from "node:test";

// A CLIENT-render smoke test for the data-driven Detailing tab (per the jsdom
// client-render recipe): it mounts DetailingPanel against a store seeded with an
// engine advertising an UNUSUAL joint type, and asserts (a) the panel renders
// EXACTLY that advertised joint type — proving it is data-driven, not hardcoded —
// and (b) it does not trip the zustand unstable-selector infinite-loop crash
// ("Maximum update depth exceeded"), which every selector here is written to
// avoid (each returns a stored ref, never a fresh array/object).
//
// jsdom is a THROWAWAY diagnostic dep (not in package.json); when it isn't
// installed the test skips rather than breaking the suite.

// jsdom is optional + type-less here (not in package.json), so reference it via a
// runtime dynamic import behind a loose constructor type — a static
// `import("jsdom")` type reference would fail `tsc` when it isn't installed.
type JsdomCtor = new (
  html: string,
  opts?: Record<string, unknown>,
) => { window: unknown };
let JSDOM: JsdomCtor | undefined;
try {
  ({ JSDOM } = (await import("jsdom" as string)) as { JSDOM: JsdomCtor });
} catch {
  JSDOM = undefined;
}

test(
  "DetailingPanel renders advertised joint types without an unstable-selector crash",
  { skip: JSDOM ? false : "jsdom not installed (throwaway diagnostic dep)" },
  async () => {
    const dom = new JSDOM!(
      "<!doctype html><html><body><div id='root'></div></body></html>",
      { url: "http://localhost/", pretendToBeVisual: true },
    );
    const w = dom.window as unknown as Window & typeof globalThis;
    const g = globalThis as Record<string, unknown>;
    g.window = w;
    g.document = w.document;
    g.self = w;
    g.HTMLElement = w.HTMLElement;
    g.Element = w.Element;
    g.Node = w.Node;
    g.DocumentFragment = w.DocumentFragment;
    g.getComputedStyle = w.getComputedStyle.bind(w);
    g.requestAnimationFrame = (cb: FrameRequestCallback) =>
      setTimeout(() => cb(Date.now()), 0) as unknown as number;
    g.cancelAnimationFrame = (id: number) => clearTimeout(id);
    g.matchMedia =
      w.matchMedia ??
      (() => ({
        matches: false,
        addEventListener() {},
        removeEventListener() {},
        addListener() {},
        removeListener() {},
      }));
    g.sessionStorage = w.sessionStorage;
    g.localStorage = w.localStorage;
    try {
      g.navigator = w.navigator;
    } catch {
      /* node exposes a read-only global navigator; leave it */
    }

    // Import AFTER the DOM globals exist (module load touches browser globals).
    const React = (await import("react")).default;
    const { act } = await import("react");
    const { createRoot } = await import("react-dom/client");
    const { useCellBuilderStore } = await import("@/state/cellBuilderStore");
    const { resolveDetailingOptions } = await import(
      "@/utils/cellbuilder/detailingOptions"
    );
    const DetailingPanel = (await import("@/components/viewer/DetailingPanel"))
      .default;

    // An engine advertising a DELIBERATELY unusual joint type — if the panel were
    // hardcoded to the built-in slugs this would not appear.
    const engine = {
      slug: "custom-detailing",
      name: "custom detailing",
      description: "test engine",
      inprocess: true,
      origin: "code" as const,
      joint_types: [
        {
          slug: "wibble_joint",
          name: "Wibble joint",
          default_enabled: true,
          fields: [
            { name: "wibble_mm", type: "number" as const, default: 7, min: 1, max: 30, unit: "mm" },
            { name: "flavour", type: "enum" as const, default: "B", options: ["A", "B", "C"] },
          ],
        },
      ],
    };

    useCellBuilderStore.setState({
      selectedDetailing: "custom-detailing",
      detailingEngines: [engine],
      detailingOptions: resolveDetailingOptions(engine, {}),
      detailingJointCounts: { wibble_joint: 3 },
    });

    const container = w.document.getElementById("root")!;
    const root = createRoot(container);

    let loopError = false;
    const origErr = console.error;
    console.error = (...a: unknown[]) => {
      if (String(a[0] ?? "").includes("Maximum update depth")) loopError = true;
    };
    try {
      await act(async () => {
        root.render(React.createElement(DetailingPanel));
      });
    } finally {
      console.error = origErr;
    }

    assert.equal(loopError, false, "unstable-selector infinite render loop");
    const text = container.textContent ?? "";
    assert.ok(text.includes("Wibble joint"), "renders the advertised joint type");
    assert.ok(text.includes("Detected joints: 3"), "renders the detected readout");
    // Data-driven: a built-in joint name it was NOT given must not appear.
    assert.ok(!text.includes("Girder–girder gusset"), "no hardcoded joint slugs");

    act(() => root.unmount());
  },
);
