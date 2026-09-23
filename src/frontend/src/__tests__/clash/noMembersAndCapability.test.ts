// Two small, independent pins:
//
//   - `counts.members === 0` is a SUCCESSFUL answer ("this source has no beams or plates"), not
//     an error state -- `noMembersSentence` must return the sentence in that case (from
//     `warnings[0]` when the check supplied one, else a default), and must return `null` whenever
//     the check found members OR never measured them at all (`counts.members` absent) -- an
//     absent count means "didn't look", which is a different fact from "looked, found zero".
//
//   - `isSpecAvailable` -- a spec with no `capability` (a built-in, runs in core's default pool)
//     is always offered; a spec whose `capability` no live pool advertises is NOT offered, once
//     the caller actually has a live union to check against.
//
// See `parseClashResult.test.ts` for why the browser globals are stubbed before the dynamic
// import.

import assert from "node:assert/strict";
import { test } from "node:test";

const memory = new Map<string, string>();
const storage = {
  getItem: (k: string): string | null => (memory.has(k) ? (memory.get(k) as string) : null),
  setItem: (k: string, v: string): void => void memory.set(k, String(v)),
  removeItem: (k: string): void => void memory.delete(k),
  clear: (): void => memory.clear(),
  key: (): string | null => null,
  get length(): number {
    return memory.size;
  },
};
const globals = globalThis as unknown as { sessionStorage: unknown; localStorage: unknown };
globals.sessionStorage = storage;
globals.localStorage = storage;

const { parseClashResult, noMembersSentence, isSpecAvailable } = await import("@/state/clashCheckStore");

function docWithCounts(counts: Record<string, number>, warnings: string[] = []): Record<string, unknown> {
  return {
    schema: "ada.clash/result@1",
    source_key: "files/step/box.stp",
    options: {},
    counts,
    joints: [],
    groups: [],
    provenance: {},
    warnings,
  };
}

test("counts.members === 0 renders the sentence, using the check's own warning when present", () => {
  const result = parseClashResult(
    docWithCounts({ members: 0 }, [
      "this source yielded no beams or plates, so there is nothing to find joints between. " +
        "A clash check runs on a source adapy reads into members; a shapes-only read has none.",
    ]),
  );
  const sentence = noMembersSentence(result);
  assert.ok(sentence);
  assert.match(sentence!, /no beams or plates/);
});

test("counts.members === 0 with no warning still renders a sentence, not nothing", () => {
  const result = parseClashResult(docWithCounts({ members: 0 }));
  const sentence = noMembersSentence(result);
  assert.ok(sentence);
  assert.match(sentence!, /no beams or plates/i);
});

test("counts.members > 0 renders no sentence -- the groups table is the answer", () => {
  const result = parseClashResult(docWithCounts({ members: 4, joints: 1 }));
  assert.equal(noMembersSentence(result), null);
});

test("counts.members absent (the pass never ran) renders no sentence either -- 'didn't look' is not 'looked, found zero'", () => {
  const result = parseClashResult(docWithCounts({ joints: 0 }));
  assert.equal(noMembersSentence(result), null);
});

test("a built-in spec (capability null) is always offered", () => {
  const spec = { spec: "builtin.girder_gusset", capability: null, tags: [], priority: 10 };
  assert.equal(isSpecAvailable(spec, null), true);
  assert.equal(isSpecAvailable(spec, new Set()), true);
  assert.equal(isSpecAvailable(spec, new Set(["anything"])), true);
});

test("a capability-bearing spec is offered when there is no live union to check (fails open)", () => {
  const spec = { spec: "external.box_spec", capability: "box-pool", tags: [], priority: 5 };
  assert.equal(isSpecAvailable(spec, null), true);
});

test("a capability-bearing spec is NOT offered once a live union is known and it isn't in it", () => {
  const spec = { spec: "external.box_spec", capability: "box-pool", tags: [], priority: 5 };
  assert.equal(isSpecAvailable(spec, new Set(["other-pool"])), false);
});

test("a capability-bearing spec IS offered when the live union names it", () => {
  const spec = { spec: "external.box_spec", capability: "box-pool", tags: [], priority: 5 };
  assert.equal(isSpecAvailable(spec, new Set(["box-pool", "other-pool"])), true);
});
