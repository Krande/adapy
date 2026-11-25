// Scope-gated plugins (plugin API 1.41.0): a plugin that opts in with `access` is hidden by core -- its
// top-bar buttons, panels, property sections and commands -- until the server says it is on for the open
// scope or for the user, and asked again on every scope switch.
import assert from "node:assert/strict";
import {beforeEach, test} from "node:test";

const R = await import("@/plugins/registry");
const A = await import("@/plugins/pluginAccess");
const P = await import("@/plugins/propertyProviders");
const C = await import("@/layout/commands");
const {detailText} = await import("@/services/api/client");
const M = await import("@/components/admin/pluginAccessModel");

type Ctx = import("@/plugins/registry").AdaPluginContext;
const ctx = {pluginId: ""} as unknown as Ctx;

const topBar = () => R.getPanelsForRegion("top-panel", ctx, {includePlacedElsewhere: true}).map((e) => e.panel.id);

function register(id: string, access?: import("@/plugins/pluginAccess").PluginAccessSpec) {
    R.registerPlugin({
        id,
        access,
        topBarButtons: [{id: "button", region: "top-panel", title: id, render: () => null}],
    } as never);
}

beforeEach(() => {
    R.resetRegistry();
    A.resetPluginAccess();
    C.resetCommands();
});

test("the plugin API has scope-gated plugins: 1.41.0 or later", () => {
    const [major, minor] = R.PLUGIN_API_VERSION.split(".").map(Number);
    assert.ok(major > 1 || (major === 1 && minor >= 41), R.PLUGIN_API_VERSION);
});

test("a registration names its gate: its own id, a shared one, or none", () => {
    assert.equal(A.accessGateOf("robot-sim", undefined), null);
    assert.equal(A.accessGateOf("robot-sim", "scope"), "robot-sim");
    assert.equal(A.accessGateOf("robot-sim-depth", {gate: "Robot-Sim"}), "robot-sim");
    // A malformed declaration is gated under the plugin id: a typo must not open the plugin.
    assert.equal(A.accessGateOf("p", {gate: "not a gate"} as never), "p");
});

test("a gated plugin's top-bar button is hidden until the answer says it is on; an ungated one never is", () => {
    register("plain");
    register("gated", "scope");
    assert.deepEqual(topBar(), ["plain:button"], "hidden before any answer");

    A.setPluginAccess({scope: "shared", enforced: true, enabled: []}, "shared");
    assert.deepEqual(topBar(), ["plain:button"], "hidden when the scope has it off");

    A.setPluginAccess({scope: "shared", enforced: true, enabled: ["gated"]}, "shared");
    assert.deepEqual(topBar().sort(), ["gated:button", "plain:button"], "shown once it is on");
    assert.equal(R.getPlugin("gated")?.accessGate, "gated");
});

test("a server that gates nothing shows every plugin", () => {
    register("gated", "scope");
    A.setPluginAccess({scope: "shared", enforced: false, enabled: []}, "shared");
    assert.deepEqual(topBar(), ["gated:button"]);
});

test("several registrations share one switch", () => {
    register("robot-sim", "scope");
    register("robot-sim-depth", {gate: "robot-sim"});
    A.setPluginAccess({scope: "shared", enforced: true, enabled: ["robot-sim"]}, "shared");
    assert.deepEqual(topBar().sort(), ["robot-sim-depth:button", "robot-sim:button"]);
});

test("the answer is asked again on a scope switch, and a stale one is dropped", async () => {
    register("gated", "scope");
    const asked: string[] = [];
    const on: Record<string, string[]> = {shared: ["gated"], "project:p1": []};
    let release: (() => void) | null = null;
    A.setPluginAccessFetcher(async (scope) => {
        asked.push(scope);
        if (scope === "slow") await new Promise<void>((r) => (release = r));
        return {scope, enforced: true, enabled: on[scope] ?? []};
    });
    let told = 0;
    const off = A.subscribePluginAccess(() => told++);

    await A.refreshPluginAccess("shared");
    assert.deepEqual(topBar(), ["gated:button"]);
    await A.refreshPluginAccess("project:p1");
    assert.deepEqual(topBar(), [], "off in the other scope");
    assert.ok(told >= 2, "listeners told on each change");

    // A switch back and forth: the slow, older answer must not win.
    const slow = A.refreshPluginAccess("slow");
    await A.refreshPluginAccess("shared");
    release!();
    await slow;
    assert.equal(A.pluginAccessState().scope, "shared");
    assert.deepEqual(topBar(), ["gated:button"]);
    assert.deepEqual(asked, ["shared", "project:p1", "slow", "shared"]);
    off();
});

test("a failed ask hides the gated plugins", async () => {
    register("gated", "scope");
    A.setPluginAccess({scope: "shared", enforced: true, enabled: ["gated"]}, "shared");
    A.setPluginAccessFetcher(async () => {
        throw new Error("503");
    });
    await A.refreshPluginAccess("shared");
    assert.deepEqual(topBar(), []);
    assert.equal(A.pluginAccessState().error, "503");
});

test("nothing is asked until something gated is registered", () => {
    let asked = 0;
    A.whenGatedRegistered(() => asked++);
    register("plain");
    assert.equal(asked, 0);
    register("gated", "scope");
    register("gated-2", "scope");
    assert.equal(asked, 1, "once, on the first");
    assert.equal(A.anyGatedRegistration(), true);
});

test("a gated Selected Object section is left out where its plugin is off", () => {
    const sel = {kind: "mesh", name: "x", meshName: "x", count: 1, hasEntities: true, cellBuilderActive: false} as never;
    const section = (id: string, access?: string) => ({id, match: () => true, render: () => null, access});
    const plugins = [section("simulate", "robot-sim"), section("plain")];
    const ids = () => P.providersFor(sel, [], plugins).map((p) => p.id);
    assert.deepEqual(ids(), ["plain"]);
    A.setPluginAccess({scope: "shared", enforced: true, enabled: ["robot-sim"]}, "shared");
    assert.deepEqual(ids(), ["simulate", "plain"]);
});

test("a registered section's list is rebuilt when the switch changes", () => {
    const remove = P.registerPropertyProvider({id: "t-sec", match: () => true, render: () => null, access: "g"});
    const before = P.listPropertyProviders();
    A.setPluginAccess({scope: "shared", enforced: true, enabled: ["g"]}, "shared");
    assert.notEqual(P.listPropertyProviders(), before, "a new array, so the panel renders again");
    remove();
});

test("a gated command is neither listed nor run where its plugin is off", () => {
    let ran = 0;
    C.registerCommand({id: "t:simulate", label: "Simulate", access: "robot-sim", run: () => ran++}, "t");
    const listed = () => C.listCommands().some((c) => c.id === "t:simulate");
    assert.equal(listed(), false);
    assert.equal(C.runCommand("t:simulate"), false);
    A.setPluginAccess({scope: "shared", enforced: true, enabled: ["robot-sim"]}, "shared");
    assert.equal(listed(), true);
    assert.equal(C.runCommand("t:simulate"), true);
    assert.equal(ran, 1);
});

test("ctx.access reads the same state", () => {
    let gate: string | null = "robot-sim";
    const access = A.makePluginAccess(() => gate, () => "shared");
    const seen: boolean[] = [];
    const off = access.subscribe((on) => seen.push(on));
    assert.equal(access.gate, "robot-sim");
    assert.equal(access.loaded(), false);
    assert.equal(access.enabled(), false);
    A.setPluginAccess({scope: "shared", enforced: true, enabled: ["robot-sim"]}, "shared");
    assert.equal(access.enabled(), true);
    assert.deepEqual(seen, [true]);
    off();
    gate = null; // an ungated plugin is always on
    assert.equal(access.enabled(), true);
    assert.equal(access.loaded(), true);
});

test("a refusal's sentence is shown, whatever its code", () => {
    const body = JSON.stringify({detail: {code: "plugin_access_off", plugin: "robot-sim", message: "not turned on"}});
    assert.equal(detailText(body), "not turned on");
});

test("Admin > Plugins: gates from every source, and what each switch says", () => {
    const rows = [
        {principal_kind: "scope", principal: "shared", plugin_id: "a", enabled: true},
        {principal_kind: "user", principal: "u1", plugin_id: "a", enabled: false},
        {principal_kind: "user", principal: "u2", plugin_id: "c", enabled: true},
    ] as never[];
    assert.deepEqual(M.gateIds([{id: "b", sources: ["switch"]}], ["a", null], rows), ["a", "b", "c"]);
    assert.equal(M.scopeSwitch(rows, "shared", "a"), true);
    assert.equal(M.scopeSwitch(rows, "projects/x", "a"), false);
    assert.equal(M.userSwitch(rows, "u1", "a"), "off");
    assert.equal(M.userSwitch(rows, "u2", "c"), "on");
    assert.equal(M.userSwitch(rows, "u3", "a"), "inherit");
    assert.deepEqual(M.principalsWithRows(rows, "user"), ["u1", "u2"]);
    assert.equal(M.summary(rows, "scope", "shared"), "a");
    assert.equal(M.summary(rows, "user", "u1"), "a: off");
    assert.equal(M.summary(rows, "user", "u9"), "as the scope says");
});
