// Convex contact is the default clash engine; core's connectivity check is the fallback, said so.
//   - a session that never chose runs convex contact while its wasm module loads or a worker lists it,
//     and core's (with a note) where neither can run it;
//   - a choice the user made is kept over the default;
//   - a deployment that does not list convex contact still offers it where this viewer runs it;
//   - convex contact measures in the browser by default, core's on the server.
//
// Browser globals are stubbed before the dynamic import for the reason given in
// `parseClashResult.test.ts` -- `@/state/clashCheckStore` reaches `sessionStorage` at load.

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

const {
  BUILTIN_CHECKER,
  BROWSER_CONTACT_CHECKER,
  CONTACT_UNAVAILABLE_NOTE,
  resolveChecker,
  runsInBrowser,
  useClashCheckStore,
  withBrowserContact,
} = await import("@/state/clashCheckStore");
const { CONTACT_ORIGIN } = await import("@/services/clash/contactRuns");

const CORE_ONLY = [{ slug: "adapy", name: "adapy", uses_core_options: true }];
const BOTH = [...CORE_ONLY, { slug: CONTACT_ORIGIN, name: CONTACT_ORIGIN, uses_core_options: false }];

test("a new session's engine is convex contact", () => {
  const s = useClashCheckStore.getState();
  assert.equal(s.checker, CONTACT_ORIGIN);
  assert.equal(s.checkerChosen, false);
  assert.equal(s.checkerNote, null);
  assert.equal(s.inBrowser, null);
});

test("not chosen: convex contact where it can run, core's with a note where it cannot", () => {
  const base = { checker: CONTACT_ORIGIN, checkerChosen: false };
  // The wasm module loads: convex contact, whatever the deployment lists.
  assert.deepEqual(resolveChecker({ ...base, availableCheckers: CORE_ONLY, contactWasm: true }), { checker: CONTACT_ORIGIN, note: null });
  // Not probed yet: still the default, the run probes first.
  assert.deepEqual(resolveChecker({ ...base, availableCheckers: null, contactWasm: null }), { checker: CONTACT_ORIGIN, note: null });
  // No wasm module, but a worker lists it: convex contact, on the worker.
  assert.deepEqual(resolveChecker({ ...base, availableCheckers: BOTH, contactWasm: false }), { checker: CONTACT_ORIGIN, note: null });
  // Neither: core's, and the panel says why.
  assert.deepEqual(resolveChecker({ ...base, availableCheckers: CORE_ONLY, contactWasm: false }), {
    checker: BUILTIN_CHECKER,
    note: CONTACT_UNAVAILABLE_NOTE,
  });
  // A session that fell back follows convex contact again once it can run.
  assert.equal(resolveChecker({ checker: BUILTIN_CHECKER, checkerChosen: false, availableCheckers: BOTH, contactWasm: true }).checker, CONTACT_ORIGIN);
});

test("chosen: kept over the default; dropped to core's only when it is gone", () => {
  assert.deepEqual(resolveChecker({ checker: BUILTIN_CHECKER, checkerChosen: true, availableCheckers: BOTH, contactWasm: true }), {
    checker: BUILTIN_CHECKER,
    note: null,
  });
  assert.equal(resolveChecker({ checker: "mesh-pool", checkerChosen: true, availableCheckers: BOTH, contactWasm: true }).checker, BUILTIN_CHECKER);
  assert.deepEqual(resolveChecker({ checker: CONTACT_ORIGIN, checkerChosen: true, availableCheckers: CORE_ONLY, contactWasm: false }), {
    checker: BUILTIN_CHECKER,
    note: CONTACT_UNAVAILABLE_NOTE,
  });
});

test("setChecker is an explicit choice", () => {
  useClashCheckStore.getState().setChecker(BUILTIN_CHECKER);
  const s = useClashCheckStore.getState();
  assert.equal(s.checker, BUILTIN_CHECKER);
  assert.equal(s.checkerChosen, true);
  assert.equal(resolveChecker({ ...s, availableCheckers: BOTH, contactWasm: true }).checker, BUILTIN_CHECKER);
  useClashCheckStore.setState({ checker: CONTACT_ORIGIN, checkerChosen: false });
});

test("convex contact is offered where this viewer runs it, even when the deployment does not list it", () => {
  assert.deepEqual(withBrowserContact(CORE_ONLY, true), [...CORE_ONLY, BROWSER_CONTACT_CHECKER]);
  assert.equal(withBrowserContact(BOTH, true), BOTH);
  assert.equal(withBrowserContact(CORE_ONLY, false), CORE_ONLY);
  assert.equal(withBrowserContact(null, null), null);
  assert.deepEqual(
    withBrowserContact(null, true)?.map((c) => c.name),
    [BUILTIN_CHECKER, CONTACT_ORIGIN],
  );
  // Its settings are the ones the worker's registration advertises.
  assert.deepEqual(
    BROWSER_CONTACT_CHECKER.options?.map((o) => [o.key, o.default]),
    [
      ["security_margin", 0.01],
      ["min_contact_area", 1e-6],
    ],
  );
});

test("convex contact measures in the browser by default; core's on the server; a tick wins", () => {
  assert.equal(runsInBrowser({ checker: CONTACT_ORIGIN, inBrowser: null, contactWasm: true }), true);
  assert.equal(runsInBrowser({ checker: CONTACT_ORIGIN, inBrowser: null, contactWasm: null }), true);
  assert.equal(runsInBrowser({ checker: CONTACT_ORIGIN, inBrowser: null, contactWasm: false }), false);
  assert.equal(runsInBrowser({ checker: BUILTIN_CHECKER, inBrowser: null, contactWasm: true }), false);
  assert.equal(runsInBrowser({ checker: CONTACT_ORIGIN, inBrowser: false, contactWasm: true }), false);
  assert.equal(runsInBrowser({ checker: BUILTIN_CHECKER, inBrowser: true, contactWasm: false }), true);
});
