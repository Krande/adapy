// A shell's Builder defaults: the engine and blueprint a model gets when its doc names
// none, when the server offers them. A doc's own choice and the user's pick are kept.

import assert from "node:assert/strict";
import { afterEach, test } from "node:test";

await import("../fixtures/panelRealm");

const D = await import("@/state/cellbuilder/builderDefaults");
const { useCellBuilderStore } = await import("@/state/cellBuilderStore");
const { capabilities } = await import("@/services/capabilities");

const engine = (slug: string, origin?: "builtin" | "db" | "local") => ({ id: slug, slug, name: slug, description: null, revision: 0, origin });
type Blueprint = import("@/services/api/proceduralCatalog").ProceduralBlueprintOption;
const blueprint = (slug: string, name = slug) => ({ slug, name, description: "" }) as unknown as Blueprint;

const ENGINES = [engine("adapy-default", "builtin"), engine("tool-engine", "local")];
const BLUEPRINTS: Record<string, ReturnType<typeof blueprint>[]> = {
  "adapy-default": [blueprint("steel_stru")],
  "tool-engine": [blueprint("frame"), blueprint("plate", "Stiffened plate")],
};

const prefer = () =>
  D.setBuilderDefaults({
    engine: (es) => es.find((e) => e.slug === "tool-engine" && e.origin === "local")?.slug ?? null,
    blueprint: (eng, bs) => (eng === "tool-engine" ? bs.find((b) => b.name === "Stiffened plate")?.slug ?? null : null),
  });

function stubCatalogs(engines = ENGINES): void {
  const proc = capabilities.procedural as unknown as Record<string, unknown>;
  proc.listCatalog = async (_scope: string, kind: string) => (kind === "engines" ? engines : []);
  proc.listBlueprints = async (_scope: string, eng: string) => BLUEPRINTS[eng] ?? [];
}

const settle = () => new Promise((r) => setTimeout(r, 20));

afterEach(() => {
  D.setBuilderDefaults(null);
  useCellBuilderStore.getState().close();
});

test("a default not offered counts as none", () => {
  prefer();
  assert.equal(D.defaultEngine(ENGINES), "tool-engine");
  assert.equal(D.defaultEngine([engine("adapy-default", "builtin")]), null);
  assert.equal(D.defaultBlueprint("tool-engine", BLUEPRINTS["tool-engine"]), "plate");
  assert.equal(D.defaultBlueprint("adapy-default", BLUEPRINTS["adapy-default"]), null);
  D.setBuilderDefaults(null);
  assert.equal(D.defaultEngine(ENGINES), null);
});

test("a model whose doc names neither takes the shell's engine and blueprint", async () => {
  stubCatalogs();
  prefer();
  useCellBuilderStore.getState().open("m1", "new", 0, { spaces: [] } as never);
  await settle();
  const s = useCellBuilderStore.getState();
  assert.equal(s.selectedEngine, "tool-engine");
  assert.equal(s.selectedBlueprint, "plate");
  assert.equal(s.dirty, false, "a default is not an edit");
});

test("a doc that names its engine and blueprint keeps them", async () => {
  stubCatalogs();
  prefer();
  useCellBuilderStore.getState().open("m2", "own", 1, { spaces: [], engine: "adapy-default", blueprint_name: "steel_stru" } as never);
  await settle();
  const s = useCellBuilderStore.getState();
  assert.equal(s.selectedEngine, "adapy-default");
  assert.equal(s.selectedBlueprint, "steel_stru");
});

test("without the engine on the server, core's defaults stay", async () => {
  stubCatalogs([engine("adapy-default", "builtin")]);
  prefer();
  useCellBuilderStore.getState().open("m3", "plain", 0, { spaces: [] } as never);
  await settle();
  assert.equal(useCellBuilderStore.getState().selectedEngine, "adapy-default");
});
