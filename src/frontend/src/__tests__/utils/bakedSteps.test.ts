import assert from "node:assert/strict";
import { test } from "node:test";

import { unbakedStepsNote } from "../../utils/scene/fea/bakedSteps";

const offered = [1, 2, 3, 4].map((n) => ({ n, name: `lc${n}` }));

test("a bake of every step says nothing", () => {
  assert.equal(unbakedStepsNote({ result_cases: offered }), null);
  assert.equal(unbakedStepsNote(null), null);
});

test("a bake of the model only says no case is baked, with the producer's hint", () => {
  const note = unbakedStepsNote({ baked_steps: [], result_cases: offered, baked_steps_hint: "run X --cases N" });
  assert.equal(
    note,
    "Result fields are not baked for any case: only the model is shown. To bake a case: run X --cases N",
  );
});

test("a bake of some cases names them", () => {
  assert.equal(
    unbakedStepsNote({ baked_steps: [3, 1], result_cases: offered }),
    "Result fields are baked for cases 1, 3 only of the 4 offered; other cases are not baked.",
  );
  assert.equal(
    unbakedStepsNote({ baked_steps: [2], result_cases: offered }),
    "Result fields are baked for case 2 only of the 4 offered; other cases are not baked.",
  );
});

test("a bake that holds every offered case says nothing", () => {
  assert.equal(unbakedStepsNote({ baked_steps: [1, 2, 3, 4], result_cases: offered }), null);
});
