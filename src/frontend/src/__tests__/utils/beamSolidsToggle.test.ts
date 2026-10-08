import assert from "node:assert/strict";
import { beforeEach, test } from "node:test";

import type { FeaManifest } from "../../services/viewerApi";
import { useFeaAnimationStore } from "../../state/feaAnimationStore";
import {
  BEAM_SOLIDS_PERF_OPT_OUT,
  beamSolidsToggleState,
  describeBeamSolidsFailure,
} from "../../utils/scene/fea/streaming/beamSolidsToggle";

// The "Beams as solid" toggle used to be offered on the manifest's word alone.
// A compact-baked result whose expander could not load (the page could not
// fetch its /wasm/ module) built no solid mesh, logged a console warning, and
// left a checkbox that ticked and did nothing. The toggle now follows what was
// BUILT: the loader records why nothing was, and the toggle shows that.

function manifest(mesh: Partial<FeaManifest["mesh"]>): FeaManifest {
  return { mesh: { url: "fea.mesh.glb", ...mesh } } as unknown as FeaManifest;
}

beforeEach(() => {
  useFeaAnimationStore.getState().reset();
});

test("no beam-solid artefact: no toggle", () => {
  const s = beamSolidsToggleState(manifest({}), null);
  assert.equal(s.offered, false);
  assert.equal(s.enabled, false);
  assert.equal(beamSolidsToggleState(null, null).offered, false);
});

test("built solids: the toggle is offered and live", () => {
  for (const m of [
    manifest({ beam_solids_compact_url: "fea.beam_solids.compact.bin" }),
    manifest({ beam_solids_url: "fea.beam_solids.glb" }),
  ]) {
    const s = beamSolidsToggleState(m, null);
    assert.equal(s.offered, true);
    assert.equal(s.enabled, true);
    assert.doesNotMatch(s.title, /not available/);
  }
});

test("solids named but not built: offered, disabled, and says why", () => {
  // What the dev server produced: the expander's wasm module was a 404.
  const err = new TypeError(
    "Failed to fetch dynamically imported module: http://localhost:5173/wasm/adacpp_extrude.js",
  );
  const reason = describeBeamSolidsFailure(err);
  const s = beamSolidsToggleState(
    manifest({ beam_solids_compact_url: "fea.beam_solids.compact.bin" }),
    reason,
  );
  assert.equal(s.offered, true);
  assert.equal(s.enabled, false);
  assert.match(s.title, /not available/);
  assert.match(s.title, /adacpp_extrude\.js/);
});

test("the perf opt-out reads as the user's own switch", () => {
  const s = beamSolidsToggleState(
    manifest({ beam_solids_compact_url: "fea.beam_solids.compact.bin" }),
    BEAM_SOLIDS_PERF_OPT_OUT,
  );
  assert.equal(s.enabled, false);
  assert.match(s.title, /Skip beam-solid load/);
});

test("the store records the reason per loaded model and reset clears it", () => {
  const store = useFeaAnimationStore.getState();
  assert.equal(store.beamSolidsUnavailable, null);
  store.setBeamSolidsVisible(true);
  store.setBeamSolidsUnavailable("could not be built (boom)");
  assert.equal(useFeaAnimationStore.getState().beamSolidsUnavailable, "could not be built (boom)");
  useFeaAnimationStore.getState().reset();
  assert.equal(useFeaAnimationStore.getState().beamSolidsUnavailable, null);
  // The user's preference is not a fact about the model: it survives.
  assert.equal(useFeaAnimationStore.getState().beamSolidsVisible, true);
});
