// Where the browser compiles a procedural model, per engine and level of detail
// (services/compute/proceduralBrowserEngine). A worker-advertised engine has a browser
// path exactly when a baked plugin wheel is keyed by its slug in
// /wheels/plugin-wheels-manifest.json; the detail LOD has one only for such an engine,
// with no detailing engine selected.
import {test} from "node:test";
import assert from "node:assert/strict";

import {bakedEngineWheel, browserEngineRoute, type ListedEngine} from "../../services/compute/proceduralBrowserEngine";
import type {PluginWheelsManifest} from "../../utils/pyodide/pluginWheels";

const manifest: PluginWheelsManifest = {
    plugins: {
        "demo-engine": {wheel: "demo_engine-1.0-py3-none-any.whl", register: "demo_engine.register", deps: ["adapy"]},
        "demo-job": {wheel: "demo_job-1.0-py3-none-any.whl", entrypoint: "demo_job:run"},
    },
};
const none: PluginWheelsManifest = {plugins: {}};

const engines: ListedEngine[] = [
    {id: "builtin:adapy-default", slug: "adapy-default", origin: "builtin"},
    {id: "builtin:echo", slug: "echo", origin: "builtin"},
    {id: "worker:demo-engine", slug: "demo-engine", origin: "worker"},
    {id: "worker:other-engine", slug: "other-engine", origin: "worker"},
    {id: "db-1", slug: "db-engine", origin: "db"},
    {id: "local:local-engine", slug: "local-engine", origin: "local"},
];

const route = (slug: string | null, over: Partial<Parameters<typeof browserEngineRoute>[0]> = {}) =>
    browserEngineRoute({slug, engines, manifest, lod: "sim", detailing: "none", ...over});

test("the default engine compiles in the browser as before", () => {
    assert.deepEqual(route(null), {kind: "default"});
    assert.deepEqual(route(""), {kind: "default"});
    assert.deepEqual(route("adapy-default"), {kind: "default"});
});

test("a built-in engine dispatches by slug", () => {
    assert.deepEqual(route("echo"), {kind: "builtin", engine: "echo"});
});

test("a worker-advertised engine a baked wheel registers runs in the browser", () => {
    assert.deepEqual(route("demo-engine"), {kind: "baked", engine: "demo-engine"});
});

test("a worker-advertised engine with no baked wheel stays server-only", () => {
    assert.equal(route("other-engine").kind, "server-only");
    assert.equal(route("demo-engine", {manifest: none}).kind, "server-only");
});

test("a baked engine needs no listing: the manifest is enough", () => {
    assert.deepEqual(route("demo-engine", {engines: []}), {kind: "baked", engine: "demo-engine"});
});

test("database and local engines ask the server's resolve", () => {
    assert.deepEqual(route("db-engine"), {kind: "resolve", engineId: "db-1"});
    assert.deepEqual(route("local-engine"), {kind: "resolve", engineId: "local:local-engine"});
});

test("a local engine a baked wheel registers runs in the browser too", () => {
    const m: PluginWheelsManifest = {plugins: {"local-engine": {wheel: "l-1.0-py3-none-any.whl", register: "l.register"}}};
    assert.deepEqual(route("local-engine", {manifest: m}), {kind: "baked", engine: "local-engine"});
});

test("a built-in slug is never taken over by a manifest entry of the same name", () => {
    const m: PluginWheelsManifest = {plugins: {echo: {wheel: "echo-1.0-py3-none-any.whl", register: "echo.register"}}};
    assert.deepEqual(route("echo", {manifest: m}), {kind: "builtin", engine: "echo"});
});

test("the detail LOD runs in the browser only for a baked engine without a detailing engine", () => {
    assert.deepEqual(route("demo-engine", {lod: "detail"}), {kind: "baked", engine: "demo-engine"});
    assert.deepEqual(route("demo-engine", {lod: "detail", detailing: ""}), {kind: "baked", engine: "demo-engine"});
    assert.deepEqual(route("demo-engine", {lod: "detail", detailing: null}), {kind: "baked", engine: "demo-engine"});
    assert.equal(route("demo-engine", {lod: "detail", detailing: "demo-detailing"}).kind, "server-only");
    for (const slug of [null, "adapy-default", "echo", "other-engine", "db-engine"]) {
        assert.equal(route(slug, {lod: "detail"}).kind, "server-only", `${slug} detail`);
    }
});

test("a plugin-job wheel (no register hook) is not an engine", () => {
    const m: PluginWheelsManifest = {plugins: {"other-engine": {wheel: "o-1.0-py3-none-any.whl", entrypoint: "o:run"}}};
    assert.equal(route("other-engine", {manifest: m}).kind, "server-only");
    assert.equal(bakedEngineWheel(manifest, "demo-job"), false);
});

test("bakedEngineWheel matches the manifest key only", () => {
    assert.equal(bakedEngineWheel(manifest, "demo-engine"), true);
    assert.equal(bakedEngineWheel(manifest, "demo_engine"), false);
    assert.equal(bakedEngineWheel(manifest, "adapy-default"), false);
    assert.equal(bakedEngineWheel(manifest, null), false);
    assert.equal(bakedEngineWheel(manifest, "toString"), false);
    assert.equal(bakedEngineWheel({plugins: {}} as PluginWheelsManifest, "demo-engine"), false);
});
