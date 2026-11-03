import assert from "node:assert/strict";
import {test} from "node:test";

import {MODE_TO_TAB, TAB_META} from "@/components/info_box_scene/sceneTabs";
import {
    LEGEND_TAB,
    MODEL_TAB,
    RESULTS_TAB,
    defaultSimTab,
    feModelLoaded,
    hasFemConcepts,
    resolveSimTab,
    simTabKey,
    simulationButtonShown,
} from "@/components/simulation/simPanelTabs";
import {useAnimationStore} from "@/state/animationStore";
import {useSimPanelStore, openSimulationPanel} from "@/state/simPanelStore";

// The Simulation panel carries the FE model (masses, supports, load scenarios) as a
// second tab beside the result controls; the Scene panel no longer has a FEM tab.

const all = () => true;
const withResults = {hasResults: true, hasModel: true};
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
    assert.equal(resolveSimTab({tab: MODEL_TAB, key}, key, all, withResults), MODEL_TAB);
    // Another model, or results arriving on this one: the default again.
    assert.equal(resolveSimTab({tab: MODEL_TAB, key}, simTabKey("other", true), all, withResults), RESULTS_TAB);
    assert.notEqual(simTabKey("deck", false), key);
});

test("a remembered tab that went away falls back to the default", () => {
    const key = simTabKey("deck", true);
    assert.equal(resolveSimTab({tab: LEGEND_TAB, key}, key, (t) => t !== LEGEND_TAB, withResults), RESULTS_TAB);
    assert.equal(resolveSimTab({tab: "some-plugin-tab", key}, key, (t) => t === RESULTS_TAB, withResults), RESULTS_TAB);
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

test("an FE model without results opens on Model; with results on Results", () => {
    assert.equal(defaultSimTab({hasResults: false, hasModel: true}), MODEL_TAB);
    assert.equal(defaultSimTab({hasResults: true, hasModel: true}), RESULTS_TAB);
    assert.equal(defaultSimTab({hasResults: true, hasModel: false}), RESULTS_TAB);
    assert.equal(defaultSimTab({hasResults: false, hasModel: false}), RESULTS_TAB);
    const modelOnly = simTabKey("deck", false);
    const remembered = {tab: RESULTS_TAB, key: modelOnly};
    assert.equal(resolveSimTab(null, modelOnly, all, {hasResults: false, hasModel: true}), MODEL_TAB);
    assert.equal(resolveSimTab(remembered, modelOnly, all, {hasResults: false, hasModel: true}), RESULTS_TAB);
    // A pick made on the model alone does not carry over once results load on it.
    assert.equal(
        resolveSimTab({tab: MODEL_TAB, key: modelOnly}, simTabKey("deck", true), all, withResults),
        RESULTS_TAB,
    );
});

test("the Simulation button shows for results and for an FE model without any", () => {
    const none = {hasAnimation: false, sessionActive: false, feModel: false};
    assert.equal(simulationButtonShown(none), false);
    assert.equal(simulationButtonShown({...none, feModel: true}), true);
    assert.equal(simulationButtonShown({...none, sessionActive: true}), true);
    assert.equal(simulationButtonShown({...none, hasAnimation: true}), true);
    assert.equal(
        simulationButtonShown({...none, feModel: feModelLoaded({manifest: {fields: []}, femHasConcepts: false})}),
        true,
    );
});
