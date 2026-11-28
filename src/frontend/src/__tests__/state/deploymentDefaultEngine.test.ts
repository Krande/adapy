// The deployment's default procedural engine in the viewer: the server records it on a new
// model (and in its doc), so the Builder's Engine dropdown opens on it; a template keeps its own
// engine; a stored model keeps the one it names.

import assert from "node:assert/strict";
import { afterEach, test } from "node:test";

const { setServerState } = await import("../fixtures/panelRealm");

const React = (await import("react")).default;
const { renderToStaticMarkup } = await import("react-dom/server");
const { CompileSettingsBody } = await import("@/components/viewer/cellbuilder/panel/CompileSettingsSection");
const { useCellBuilderStore } = await import("@/state/cellBuilderStore");
const { capabilities } = await import("@/services/capabilities");
const { viewerApi } = await import("@/services/viewerApi");
const { setDialogHost } = await import("@/plugins/dialogs");
const { createAndOpenProceduralModel } = await import("@/utils/cellbuilder/newModel");
const { describeDefaultEngine } = await import("@/components/admin/ProceduralDefaultEngineSetting");

type Summary = import("@/services/api/proceduralCatalog").ProceduralEngineSummary;
const engine = (slug: string, is_default = false): Summary => ({
  id: slug === "adapy-default" ? "builtin:adapy-default" : `worker:${slug}`,
  slug,
  name: slug,
  description: null,
  revision: 0,
  is_default,
});
const ENGINES = [engine("adapy-default"), engine("example-engine", true)];

function stubCatalogs(engines = ENGINES): void {
  const proc = capabilities.procedural as unknown as Record<string, unknown>;
  proc.listCatalog = async (_scope: string, kind: string) => (kind === "engines" ? engines : []);
  proc.listBlueprints = async () => [];
  proc.resyncEquipmentTypes = async () => ({ created: [], updated: [], unchanged: [], skipped: [], changes: {} });
}

const api = viewerApi as unknown as Record<string, unknown>;
const saved = { create: api.createProceduralModel };
const settle = () => new Promise((r) => setTimeout(r, 20));

afterEach(() => {
  api.createProceduralModel = saved.create;
  setDialogHost(null);
  useCellBuilderStore.getState().close();
});

test("a new model opens on the deployment's default engine the server recorded for it", async () => {
  stubCatalogs();
  const calls: unknown[][] = [];
  api.createProceduralModel = async (...args: unknown[]) => {
    calls.push(args);
    // What the server answers on a deployment whose default is example-engine.
    return { id: "m-new", name: "fresh", revision: 0, engine: "example-engine", doc: { spaces: [], equipments: [], engine: "example-engine" } };
  };
  setDialogHost({ prompt: async () => "fresh" });

  assert.equal(await createAndOpenProceduralModel("shared"), true);
  await settle();
  // The client names no engine: choosing it is the server's job.
  assert.deepEqual(calls, [["shared", "fresh"]]);
  const s = useCellBuilderStore.getState();
  assert.equal(s.selectedEngine, "example-engine");
  assert.equal(s.engineIsDefault, false);

  // The Builder's Engine dropdown is preselected on it.
  setServerState(useCellBuilderStore, { selectedEngine: s.selectedEngine, engines: s.engines, active: s.active });
  const html = renderToStaticMarkup(React.createElement(CompileSettingsBody));
  const engineSelect = /<span[^>]*>Engine<\/span><select[^>]*>([\s\S]*?)<\/select>/.exec(html)?.[1] ?? "";
  const selected = engineSelect.match(/<option[^>]* selected=""[^>]*>/)?.[0] ?? "";
  assert.match(selected, /value="example-engine"/);
});

test("a stored model keeps the engine its doc names, whatever the default is", async () => {
  stubCatalogs();
  useCellBuilderStore.getState().open("m-old", "older", 3, { spaces: [], equipments: [], engine: "adapy-default" } as never);
  await settle();
  assert.equal(useCellBuilderStore.getState().selectedEngine, "adapy-default");
});

test("a template is created for its own engine", async () => {
  const calls: unknown[][] = [];
  api.createProceduralModel = async (...args: unknown[]) => {
    calls.push(args);
    return { id: "m-tpl", name: "tpl", revision: 0, doc: { spaces: [], equipments: [] } };
  };
  const commits: unknown[] = [];
  const savedCommit = api.commitProceduralModel;
  const savedGet = api.getProceduralModel;
  api.commitProceduralModel = async (_s: string, _id: string, doc: unknown) => (commits.push(doc), { revision: 1 });
  api.getProceduralModel = async () => ({ id: "m-tpl", name: "tpl", revision: 1, doc: { spaces: [], equipments: [] } });
  try {
    const { RESTProceduralModelCapability } = await import("@/services/capabilities/rest_capabilities");
    const proc = new RESTProceduralModelCapability();
    await proc.createModelWithDoc("shared", "tpl", { spaces: [], equipments: [], engine: "adapy-default" });
    await proc.createModelWithDoc("shared", "plain", { spaces: [], equipments: [] });
  } finally {
    api.commitProceduralModel = savedCommit;
    api.getProceduralModel = savedGet;
  }
  assert.deepEqual(calls, [
    ["shared", "tpl", { engine: "adapy-default" }],
    // A doc naming no engine leaves the choice to the server (the deployment's default).
    ["shared", "plain", undefined],
  ]);
  assert.equal(commits.length, 2);
});

test("the admin row says what is in effect, and when it fell back", () => {
  assert.match(
    describeDefaultEngine({ id: "worker:example-engine", slug: "example-engine", name: "Example", configured: "example-engine", source: "env", fallback: false, note: null }),
    /Example \(example-engine\), from the deployment/,
  );
  const fell = describeDefaultEngine({
    id: "builtin:adapy-default",
    slug: "adapy-default",
    name: "adapy default",
    configured: "gone",
    source: "setting",
    fallback: true,
    note: "default procedural engine 'gone' is not available here",
  });
  assert.match(fell, /not available/);
  assert.match(describeDefaultEngine(null), /does not report/);
});
