// Playing a result is one number per frame: the deformation scale, written to whatever draws the
// result on the viewport on screen. The oscillator, the transport and the scale write it through
// the result's layer; a session registered by a three.js mesh alone still gets it on the mesh.
import assert from "node:assert/strict";
import {beforeEach, test} from "node:test";

import {useFeaAnimationStore} from "@/state/feaAnimationStore";
import {resetFeaAnimationPhase, stepFeaSweep, tickFeaAnimation, type FeaSweepState} from "@/utils/scene/fea/feaAnimationDriver";
import {setResultFactor, setResultScale, stopResults} from "@/utils/scene/fea/playbackActions";
import {hasResultTarget, setResultInfluence} from "@/utils/scene/fea/resultInfluence";
import type {ResultMeshLayer} from "@/viewport/api/resultLayer";

function fakeLayer(): ResultMeshLayer & {influences: number[]} {
    const influences: number[] = [];
    return {
        influences,
        vertexCount: 0,
        setColors() {},
        setDisplacement() {},
        setInfluence: (v) => void influences.push(v),
        setColorsVisible() {},
        setVisible() {},
        remove() {},
    };
}

const last = (xs: number[]) => xs[xs.length - 1];

function sweepState(over: Partial<FeaSweepState>): FeaSweepState {
    return {isPlaying: true, sessionActive: true, mesh: null, resultLayer: null, period: 2, range: [-1, 1], scaleFactor: 1, ...over};
}

beforeEach(() => {
    useFeaAnimationStore.getState().reset();
    resetFeaAnimationPhase();
});

test("the sweep is a sine over the field's range, times the deformation scale", () => {
    const layer = fakeLayer();
    const state = sweepState({resultLayer: layer, scaleFactor: 40});
    // A quarter of the period: the top of an eigenmode's range.
    const quarter = stepFeaSweep(state, 0, 0.5)!;
    assert.ok(Math.abs(quarter.factor - 1) < 1e-12);
    assert.ok(Math.abs(last(layer.influences) - 40) < 1e-9);
    // Three quarters: the bottom.
    const three = stepFeaSweep(state, quarter.phase, 1)!;
    assert.ok(Math.abs(three.factor + 1) < 1e-12);
    assert.ok(Math.abs(last(layer.influences) + 40) < 1e-9);
    assert.equal(three.phase, 1.5);
});

test("a static result sweeps between undeformed and fully deformed, never negative", () => {
    const layer = fakeLayer();
    const state = sweepState({resultLayer: layer, range: [0, 1]});
    let phase = 0;
    for (let i = 0; i < 40; i++) phase = stepFeaSweep(state, phase, 0.05)!.phase;
    assert.ok(Math.min(...layer.influences) >= 0);
    assert.ok(Math.max(...layer.influences) <= 1);
    assert.ok(Math.max(...layer.influences) > 0.99 && Math.min(...layer.influences) < 0.01);
});

test("nothing is swept while paused, without a session, or with nothing drawn", () => {
    const layer = fakeLayer();
    assert.equal(stepFeaSweep(sweepState({resultLayer: layer, isPlaying: false}), 0, 0.5), null);
    assert.equal(stepFeaSweep(sweepState({resultLayer: layer, sessionActive: false}), 0, 0.5), null);
    assert.equal(stepFeaSweep(sweepState({resultLayer: layer, period: 0}), 0, 0.5), null);
    assert.equal(stepFeaSweep(sweepState({}), 0, 0.5), null);
    assert.deepEqual(layer.influences, []);
});

test("a session registered by its mesh alone is scaled on the mesh's own influence", () => {
    const mesh = {morphTargetInfluences: [0]} as unknown as NonNullable<FeaSweepState["mesh"]>;
    assert.ok(hasResultTarget({resultLayer: null, mesh}));
    stepFeaSweep(sweepState({mesh, scaleFactor: 3}), 0, 0.5);
    assert.ok(Math.abs(mesh.morphTargetInfluences![0] - 3) < 1e-9);
    // With both, the layer is what draws: the mesh is not written behind its back.
    const layer = fakeLayer();
    setResultInfluence({resultLayer: layer, mesh}, 9);
    assert.deepEqual(layer.influences, [9]);
    assert.ok(Math.abs(mesh.morphTargetInfluences![0] - 3) < 1e-9);
});

test("the frame tick drives the store's result and moves the slider with it", () => {
    const layer = fakeLayer();
    useFeaAnimationStore.setState({sessionActive: true, resultLayer: layer, isPlaying: true, period: 2, range: [-1, 1], scaleFactor: 2, factor: 0});
    tickFeaAnimation(0.5);
    assert.ok(Math.abs(last(layer.influences) - 2) < 1e-9);
    assert.ok(Math.abs(useFeaAnimationStore.getState().factor - 1) < 1e-9);
});

test("the transport writes the scale where the result is drawn: slider times deformation scale, and zero on stop", () => {
    const layer = fakeLayer();
    useFeaAnimationStore.setState({sessionActive: true, resultLayer: layer, nSteps: 3, factor: 0.5, scaleFactor: 1});
    setResultScale(10);
    assert.equal(last(layer.influences), 5);
    setResultFactor(0.2);
    assert.equal(last(layer.influences), 2);
    setResultScale(NaN); // an edit in progress changes nothing
    assert.equal(layer.influences.length, 2);
    useFeaAnimationStore.setState({isPlaying: true});
    stopResults();
    assert.equal(last(layer.influences), 0);
    assert.equal(useFeaAnimationStore.getState().isPlaying, false);
    assert.equal(useFeaAnimationStore.getState().factor, 0);
});

test("the layer stays with the mesh it wraps, and goes when another mesh is registered", () => {
    const layer = fakeLayer();
    const mesh = {} as NonNullable<FeaSweepState["mesh"]>;
    const s = useFeaAnimationStore.getState();
    s.setMesh(mesh);
    s.setResultLayer(layer);
    s.setMesh(mesh);
    assert.equal(useFeaAnimationStore.getState().resultLayer, layer);
    s.setMesh({} as NonNullable<FeaSweepState["mesh"]>);
    assert.equal(useFeaAnimationStore.getState().resultLayer, null);
    s.setResultLayer(layer);
    s.reset();
    assert.equal(useFeaAnimationStore.getState().resultLayer, null);
});
