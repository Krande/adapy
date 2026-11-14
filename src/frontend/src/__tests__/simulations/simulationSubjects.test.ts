// Simulation subjects on the client: the store of offers, the picker's helpers, `ctx.simulations`,
// the label a run row shows, and the body a run is started with.

import assert from "node:assert/strict";
import { beforeEach, test } from "node:test";

import type { SimulationProviderInfo, SimulationRun } from "@/services/api/simulations";

// The API module reads the runtime config, which reads `window`.
const g = globalThis as Record<string, unknown>;
g.window ??= { location: new URL("http://localhost/"), addEventListener() {}, removeEventListener() {} };

const subjects = await import("@/state/simulationSubjects");
const { acceptingFirst, chosenSubject, doesNotTake, NO_SUBJECT, providerAccepts, subjectId, useSimulationSubjectStore } =
  subjects;
const { makePluginSimulations } = await import("@/plugins/pluginSimulations");
const { runSubjectLabel } = await import("@/services/simulations/runRow");
const { createSimulationStore } = await import("@/state/simulationStore");
const { simulationsApi } = await import("@/services/api/simulations");

const store = useSimulationSubjectStore;
type Subject = import("@/state/simulationSubjects").SimulationSubject;
const weld = (key: string, over: Partial<Subject> = {}): Subject => ({
  kind: "weld-assembly",
  key,
  scope: "user:me",
  label: key,
  ...over,
});

beforeEach(() => store.setState({ offered: [] }));

test("offers are listed newest first, and offering one again renews it instead of doubling it", () => {
  store.getState().offer(weld("a.json", { label: "A" }));
  store.getState().offer(weld("b.json"));
  store.getState().offer(weld("a.json", { label: "A again" }));
  assert.deepEqual(
    store.getState().offered.map((o) => [o.key, o.label]),
    [
      ["a.json", "A again"],
      ["b.json", "b.json"],
    ],
  );
  // The same key in another scope is another subject.
  store.getState().offer(weld("a.json", { scope: "shared" }));
  assert.equal(store.getState().offered.length, 3);
});

test("withdrawing takes the offer back, in one scope or in all", () => {
  store.getState().offer(weld("a.json"));
  store.getState().offer(weld("a.json", { scope: "shared" }));
  store.getState().offer(weld("b.json"));
  store.getState().withdraw("a.json", "shared");
  assert.deepEqual(store.getState().offered.map(subjectId), [subjectId(weld("b.json")), subjectId(weld("a.json"))]);
  store.getState().withdraw("a.json");
  store.getState().withdraw("never-offered.json");
  assert.deepEqual(store.getState().offered.map((o) => o.key), ["b.json"]);
});

test("the picker shows the newest offer until one is chosen, and none when none is wanted", () => {
  assert.equal(chosenSubject([], null), null);
  store.getState().offer(weld("a.json"));
  store.getState().offer(weld("b.json"));
  const { offered } = store.getState();
  assert.equal(chosenSubject(offered, null)?.key, "b.json");
  assert.equal(chosenSubject(offered, subjectId(weld("a.json")))?.key, "a.json");
  // A choice that was withdrawn falls back to the newest; "(none)" stays none.
  assert.equal(chosenSubject(offered, subjectId(weld("gone.json")))?.key, "b.json");
  assert.equal(chosenSubject(offered, NO_SUBJECT), null);
});

const provider = (id: string, accepts?: string[]): SimulationProviderInfo => ({
  id,
  domain: "robotics",
  engine: "fake",
  title: id,
  version: "1",
  description: null,
  kinds: [],
  parameters_schema: {},
  ...(accepts ? { accepts } : {}),
  capability: `sim-${id}`,
  online: true,
  workers: 1,
});

test("providers that accept the kind come first; an older server's, which list none, take none", () => {
  const providers = [provider("plain"), provider("old", undefined), provider("welder", ["weld-assembly"]), provider("fem", [])];
  assert.deepEqual(
    acceptingFirst(providers, weld("a.json")).map((p) => p.id),
    ["welder", "plain", "old", "fem"],
  );
  assert.deepEqual(acceptingFirst(providers, null).map((p) => p.id), ["plain", "old", "welder", "fem"]);
  assert.equal(providerAccepts(providers[2], "weld-assembly"), true);
  assert.equal(providerAccepts(providers[1], "weld-assembly"), false);
  assert.equal(doesNotTake("weld-assembly"), "does not take a weld-assembly");
});

test("ctx.simulations offers in the open scope under the plugin's name, and the handle withdraws it", () => {
  const sims = makePluginSimulations("acme-parts", () => "project:p1");
  const handle = sims.offerSubject({ kind: "weld-assembly", key: "joints/j1.json", label: " Joint 1 " });
  assert.deepEqual(store.getState().offered, [
    { kind: "weld-assembly", key: "joints/j1.json", scope: "project:p1", label: "Joint 1", source: "acme-parts" },
  ]);
  const other = sims.offerSubject({ kind: "weld-assembly", key: "j2.json", label: "", scope: "shared", source: "Studio" });
  assert.deepEqual(
    store.getState().offered.map((o) => [o.key, o.scope, o.label, o.source]),
    [
      ["j2.json", "shared", "j2.json", "Studio"],
      ["joints/j1.json", "project:p1", "Joint 1", "acme-parts"],
    ],
  );
  handle.withdraw();
  assert.deepEqual(store.getState().offered.map((o) => o.key), ["j2.json"]);
  other.withdraw();
  other.withdraw(); // twice is harmless
  assert.deepEqual(store.getState().offered, []);
  assert.throws(() => sims.offerSubject({ kind: "", key: "x", label: "x" }), /kind and a key/);
});

test("a run row names what it was started on: its offer's label while offered, else the file", () => {
  const run = (key: string | null) =>
    ({ subject: key === null ? null : { kind: "weld-assembly", key } }) as Pick<SimulationRun, "subject">;
  const offers = [weld("_dev/connections/box/ab12.weld-assembly.json", { label: "Box to box" })];
  assert.equal(runSubjectLabel(run("_dev/connections/box/ab12.weld-assembly.json"), "user:me", offers), "Box to box");
  assert.equal(runSubjectLabel(run("_dev/connections/box/ab12.weld-assembly.json"), "shared", offers), "ab12");
  assert.equal(runSubjectLabel(run("parts/joint.json.gz"), "user:me", []), "joint");
  assert.equal(runSubjectLabel(run(null), "user:me", offers), "");
  assert.equal(runSubjectLabel({} as Pick<SimulationRun, "subject">, "user:me", offers), "");
});

test("a run is started with the subject in its body, from the store down to the wire", async () => {
  const seen: unknown[] = [];
  const api = {
    listProviders: async () => ({ domains: [], providers: [] }),
    listRuns: async () => [],
    getRun: async () => {
      throw new Error("unused");
    },
    cancelRun: async () => {
      throw new Error("unused");
    },
    startRun: async (_scope: string, body: unknown) => {
      seen.push(body);
      return { run_id: "r1", status: "queued", subject: (body as { subject: SimulationRun["subject"] }).subject } as SimulationRun;
    },
  };
  const sims = createSimulationStore(api, () => () => {});
  const subject = { kind: "weld-assembly", key: "a.json" };
  const started = await sims.getState().startRun("user:me", { provider: "robot", parameters: {}, inputs: [], subject });
  assert.deepEqual(seen, [{ provider: "robot", parameters: {}, inputs: [], subject }]);
  assert.deepEqual(started?.subject, subject);

  // And the REST call sends it as JSON to the scope's simulations route.
  const calls: Array<{ url: string; body: unknown }> = [];
  const realFetch = g.fetch;
  g.fetch = async (url: unknown, init?: { body?: string }) => {
    calls.push({ url: String(url), body: JSON.parse(init?.body ?? "null") });
    return new Response(JSON.stringify({ run: { run_id: "r2", subject } }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  };
  try {
    const run = await simulationsApi.startRun("user:me", { provider: "robot", subject });
    assert.deepEqual(run.subject, subject);
  } finally {
    g.fetch = realFetch;
  }
  assert.match(calls[0].url, /\/scopes\/user%3Ame\/simulations$/);
  assert.deepEqual(calls[0].body, { provider: "robot", subject });
});
