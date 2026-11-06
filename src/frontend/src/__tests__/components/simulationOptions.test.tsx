// The Simulation panel's options: absent, the panel renders exactly as it did; present, each
// does its one thing.

import assert from "node:assert/strict";
import { test } from "node:test";

const { setServerState } = await import("../fixtures/panelRealm");

const React = (await import("react")).default;
const { renderToStaticMarkup } = await import("react-dom/server");
const { AdaViewerProvider } = await import("@/state/AdaViewerContext");
const { SimulationBody, AnimationControls } = await import("@/components/simulation/SimulationControls");
const { withoutDockedTabs } = await import("@/components/simulation/simPanelTabs");
const { useFeaAnimationStore } = await import("@/state/feaAnimationStore");

type Props = import("@/components/simulation/SimulationControls").SimulationBodyProps;
type Display = import("@/components/simulation/SimulationControls").ResultControlsOptions;

const field = {
    name_canonical: "DISP",
    kind: "vector3",
    category: "displacement",
    components: ["DX", "DY", "DZ"],
    steps: [{ i: 0, value: 1, label: "1" }],
    analysis_kind: "static",
};
const manifest = {
    fields: [field],
    mesh: { beam_solids_url: "solids.glb" },
    baked_steps: [1],
    result_cases: [{ n: 1 }, { n: 2 }],
};

setServerState(useFeaAnimationStore, {
    sessionActive: true,
    sourceName: "probe",
    manifest: manifest as never,
    fieldName: "DISP",
    nSteps: 1,
});

function render(props?: Props): string {
    return renderToStaticMarkup(
        React.createElement(AdaViewerProvider, null, React.createElement(SimulationBody, props)),
    );
}

function controls(props?: Display): string {
    return renderToStaticMarkup(
        React.createElement(AdaViewerProvider, null, React.createElement(AnimationControls, props)),
    );
}

test("without options the panel renders as with the defaults spelled out", () => {
    const plain = render();
    assert.match(plain, /title="Visualisation options"/);
    assert.equal(render({}), plain);
    assert.equal(
        render({ layout: "panel", showWarpToggle: true, showBeamSolids: true, showColorToggle: false, excludeDockedTabs: false }),
        plain,
    );
    assert.match(plain, /Beams as solid/);
    assert.doesNotMatch(plain, /Result colours/);
});

test("inline is one row with the options always shown and no gear", () => {
    const html = controls({ layout: "inline" });
    assert.match(html, /^<div class="flex flex-row flex-nowrap items-center/);
    assert.doesNotMatch(html, /title="Visualisation options"/);
    assert.match(html, /Colormap/);
    assert.match(html, /Warp by displacement/);
});

test("the warp, beam-solid and colouring toggles follow their options", () => {
    assert.doesNotMatch(controls({ layout: "inline", showWarpToggle: false }), /Warp by displacement/);
    assert.doesNotMatch(render({ showBeamSolids: false }), /Beams as solid/);
    assert.match(controls({ layout: "inline", showColorToggle: true }), /Result colours/);
});

test("AnimationControls in the panel layout is the panel's result controls", () => {
    const html = controls();
    assert.match(html, /title="Visualisation options"/);
    assert.ok(render().includes(html), "the panel should contain exactly these controls");
});

test("a partial bake says so: in full in the panel, its first clause inline", () => {
    const note = "Result fields are baked for case 1 only of the 2 offered";
    assert.match(render(), new RegExp(`data-testid="fea-unbaked-note">${note}; other cases are not baked.<`));
    assert.match(controls({ layout: "inline" }), new RegExp(`data-testid="fea-unbaked-note" title="${note};[^"]*">${note}<`));
});

test("docked plugin tabs can be left out", () => {
    const tabs = [
        { panel: { id: "a", region: "fem-sidebar" } },
        { panel: { id: "b", dock: "right" } },
        { panel: { id: "c", region: "bottom" } },
    ];
    assert.deepEqual(withoutDockedTabs(tabs).map((t) => t.panel.id), ["a"]);
});
