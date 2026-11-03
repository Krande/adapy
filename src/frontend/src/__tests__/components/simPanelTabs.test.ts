import assert from "node:assert/strict";
import {test} from "node:test";

import {MODE_TO_TAB, TAB_META} from "@/components/info_box_scene/sceneTabs";
import {
    LEGEND_TAB,
    MODEL_TAB,
    RESULTS_TAB,
    feModelLoaded,
    hasFemConcepts,
    resolveSimTab,
    simTabKey,
} from "@/components/simulation/simPanelTabs";
import {useAnimationStore} from "@/state/animationStore";
import {useSimPanelStore, openSimulationPanel} from "@/state/simPanelStore";

// The Simulation panel carries the FE model (masses, supports, load scenarios) as a
// second tab beside the result controls; the Scene panel no longer has a FEM tab.

const all = () => true;
const noConcepts = {masses: [], bcs: [], constraints: [], scenarios: [], equipment: []};

test("an FE model is a mesh through the FEA path or a model carrying concepts", () => {
    assert.equal(feModelLoaded({manifest: null, femHasConcepts: false}), false);
    assert.equal(feModelLoaded({manifest: {}, femHasConcepts: false}), true);
    assert.equal(feModelLoaded({manifest: null, femHasConcepts: true}), true);
    assert.equal(hasFemConcepts(noConcepts), false);
    assert.equal(hasFemConcepts({...noConcepts, bcs: [{}]}), true);
});

test("the last picked tab is kept for the model it was picked on", () => {
    const key = simTabKey("deck", true);
    assert.equal(resolveSimTab({tab: MODEL_TAB, key}, key, all), MODEL_TAB);
    // Another model, or results arriving on this one: the default again.
    assert.equal(resolveSimTab({tab: MODEL_TAB, key}, simTabKey("other", true), all), RESULTS_TAB);
    assert.notEqual(simTabKey("deck", false), key);
});

test("a remembered tab that went away falls back to the default", () => {
    const key = simTabKey("deck", true);
    assert.equal(resolveSimTab({tab: LEGEND_TAB, key}, key, (t) => t !== LEGEND_TAB), RESULTS_TAB);
    assert.equal(resolveSimTab({tab: "some-plugin-tab", key}, key, (t) => t === RESULTS_TAB), RESULTS_TAB);
});

test("opening the panel on a tab shows the panel and remembers the tab", () => {
    useAnimationStore.getState().setIsControlsVisible(false);
    openSimulationPanel(MODEL_TAB);
    assert.equal(useAnimationStore.getState().isControlsVisible, true);
    assert.equal(useSimPanelStore.getState().remembered?.tab, MODEL_TAB);
    useAnimationStore.getState().setIsControlsVisible(false);
    useSimPanelStore.setState({remembered: null});
});

test("the Scene panel has no FEM tab; an opener asking for it lands on Model", () => {
    assert.equal(TAB_META.some((t) => (t.id as string) === "fem" || t.label === "FEM"), false);
    assert.equal(MODE_TO_TAB.fem, "model");
});
