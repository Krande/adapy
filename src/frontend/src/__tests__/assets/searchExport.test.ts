import assert from "node:assert/strict";
import test from "node:test";

import {
    compactQuery,
    exportTargetScopes,
    folderSuggestions,
    galleryFolderOrder,
    geometryPrompt,
    hitFromMeta,
    loginSummary,
    isExportSidecar,
    metaSidecarKey,
    normalizeFolder,
    thumbSidecarKey,
} from "@/assets/searchExport";
import type {ExportPreflight, FileMeta, PreflightCollection} from "@/services/api/searchExport";

function col(collection: string, over: Partial<PreflightCollection>): PreflightCollection {
    return {
        collection,
        hits: 0,
        ready: 0,
        missing: 0,
        listed: 0,
        unpublished: 0,
        unknown: 0,
        jobs: 0,
        missing_reason: null,
        unknown_reason: null,
        unknown_examples: [],
        ...over,
    };
}

function preflight(over: Partial<ExportPreflight>): ExportPreflight {
    return {
        geometry_provider: "geo",
        hits: 0,
        ready: 0,
        missing: 0,
        unknown: 0,
        jobs: 0,
        collections: [],
        request: {available: true, allowed: true, reason: null, plugin_id: "p", label: "Request", per_job: 500},
        sweep_running: null,
        took_ms: 1,
        ...over,
    };
}

test("the missing-geometry prompt: requestable by project, unknown apart, the sweep and permission said", () => {
    const pre = preflight({
        hits: 120,
        ready: 10,
        missing: 101,
        unknown: 9,
        jobs: 2,
        collections: [
            col("beta", {hits: 4, missing: 4, jobs: 1}),
            col("alpha", {hits: 107, ready: 10, missing: 97, jobs: 1}),
            col("gamma", {hits: 9, unknown: 9, unknown_reason: "no '/SITE-G' in the tree", unknown_examples: ["/S-1"]}),
        ],
        sweep_running: {id: "s", created_at: null},
    });
    const p = geometryPrompt(pre);
    assert.equal(p.ask, true);
    assert.equal(p.headline, "110 of 120 hits have no exported design-system geometry (by project: ALPHA 97, BETA 4)");
    assert.deepEqual(p.unknown, [{collection: "GAMMA", count: 9, reason: "no '/SITE-G' in the tree", examples: ["/S-1"]}]);
    assert.equal(p.requestable, 101);
    assert.equal(p.jobs, 2);
    assert.equal(p.cannotRequest, null);
    assert.match(p.sweepNote ?? "", /sweep is running/);

    const denied = geometryPrompt({...pre, request: {...pre.request, allowed: false, reason: "admins only"}});
    assert.equal(denied.cannotRequest, "admins only");
    assert.equal(geometryPrompt(preflight({hits: 3, ready: 3})).ask, false);
});

test("login summary counts sessions opened against sessions reused", () => {
    assert.equal(loginSummary(undefined), null);
    assert.equal(
        loginSummary([
            {nodes: 20, status: "published", login_mode: "fresh"},
            ...Array.from({length: 4}, () => ({nodes: 20, status: "published" as const, login_mode: "adopted"})),
            {nodes: 3, status: "error"},
        ]),
        "logins: 1 (adopted ×4)",
    );
    assert.equal(loginSummary([{nodes: 1, status: "nothing", login_mode: "fresh"}]), "logins: 1");
});

test("sidecar keys replace the file's last extension only", () => {
    assert.equal(metaSidecarKey("x/y/A.ifc"), "x/y/A.meta.json");
    assert.equal(thumbSidecarKey("x/y/A.ifc"), "x/y/A.thumb.png");
    assert.equal(metaSidecarKey("x/y/A.part.ifc"), "x/y/A.part.meta.json");
    // A dot in a folder is not an extension; a file with none gets the suffix appended.
    assert.equal(metaSidecarKey("x.v2/A"), "x.v2/A.meta.json");
    assert.equal(thumbSidecarKey("A"), "A.thumb.png");
    // A leading-dot name keeps its dot as part of the name.
    assert.equal(metaSidecarKey("x/.hidden"), "x/.hidden.meta.json");
});

test("sidecars and the export doc are recognised, files are not", () => {
    assert.ok(isExportSidecar("e/ALPHA/A.meta.json"));
    assert.ok(isExportSidecar("e/ALPHA/A.thumb.png"));
    assert.ok(isExportSidecar("e/_export.json"));
    assert.ok(!isExportSidecar("e/ALPHA/A.ifc"));
    assert.ok(!isExportSidecar("e/ALPHA/A.glb"));
});

test("a typed folder is normalised, and climbing out is refused", () => {
    assert.deepEqual(normalizeFolder(" /exports//joints/ "), {ok: true, folder: "exports/joints"});
    assert.deepEqual(normalizeFolder("a\\b/./c"), {ok: true, folder: "a/b/c"});
    assert.equal(normalizeFolder("  ").ok, false);
    assert.equal(normalizeFolder("a/../b").ok, false);
});

test("folder suggestions: every folder and parent, the asset store hidden, prefix matches first", () => {
    const keys = ["exports/joints/ALPHA/a.ifc", "exports/beams/b.ifc", "models/c.ifc", "assets/x/y/z.json", "top.ifc"];
    assert.deepEqual(folderSuggestions(keys), ["exports", "exports/beams", "exports/joints", "exports/joints/ALPHA", "models"]);
    assert.deepEqual(folderSuggestions(keys, "joints"), ["exports/joints", "exports/joints/ALPHA"]);
    assert.deepEqual(folderSuggestions(keys, "m"), ["models", "exports/beams"]);
    assert.deepEqual(folderSuggestions(keys, "", 2), ["exports", "exports/beams"]);
});

test("export targets: own scope, shared, projects -- never a corpus", () => {
    const got = exportTargetScopes([
        {kind: "project", id: "p2", name: "BETA"},
        {kind: "shared", id: null, name: "Shared"},
        {kind: "corpus", id: "c", name: "Corpus"},
        {kind: "user", id: "me", name: "My files"},
        {kind: "project", id: "p1", name: "ALPHA"},
    ]);
    assert.deepEqual(
        got.map((s) => s.url),
        ["user:me", "shared", "project:p1", "project:p2"],
    );
});

test("gallery order: the export doc's order, then the rest alphabetically, sidecars never", () => {
    const keys = [
        "e/ALPHA/b.ifc",
        "e/ALPHA/b.meta.json",
        "e/ALPHA/b.thumb.png",
        "e/BETA/a.ifc",
        "e/ALPHA/a.ifc",
        "e/zz-hand.ifc",
        "e/_export.json",
        "other/x.ifc",
        "e/notes.txt",
    ];
    const doc = {
        files: [
            {file: "e/BETA/a.ifc", collection: "BETA", provider: "p", node_id: "1", full_name: "a", meta: "", thumb: null},
            {file: "e/ALPHA/b.ifc", collection: "ALPHA", provider: "p", node_id: "2", full_name: "b", meta: "", thumb: null},
            {file: "e/ALPHA/gone.ifc", collection: "ALPHA", provider: "p", node_id: "3", full_name: "g", meta: "", thumb: null},
        ],
    };
    const canOpen = (k: string) => k.endsWith(".ifc");
    assert.deepEqual(galleryFolderOrder(keys, "e", doc, canOpen), ["e/BETA/a.ifc", "e/ALPHA/b.ifc", "e/ALPHA/a.ifc", "e/zz-hand.ifc"]);
    // No doc: alphabetical. A trailing slash on the folder is the same folder; "e" does not match "ee/".
    assert.deepEqual(galleryFolderOrder([...keys, "ee/x.ifc"], "e/", null, canOpen), ["e/ALPHA/a.ifc", "e/ALPHA/b.ifc", "e/BETA/a.ifc", "e/zz-hand.ifc"]);
});

test("a hit is built from provenance, or not at all", () => {
    const meta = {
        source: {
            provider: "p",
            scope: "shared",
            collection: "ALPHA",
            node_id: "n3",
            full_name: "/A/B/C",
            source_ref: null,
            revision: "r1",
            path: [
                {node_id: "n1", full_name: "/A", kind: "SITE", depth: 1},
                {node_id: "n2", full_name: "/A/B", kind: "ZONE", depth: 2},
                {node_id: "n3", full_name: "/A/B/C", kind: "EQUI", depth: 3},
            ],
        },
    } as unknown as FileMeta;
    const hit = hitFromMeta(meta)!;
    assert.equal(hit.node_id, "n3");
    assert.equal(hit.parent_id, "n2");
    assert.equal(hit.kind, "EQUI");
    assert.equal(hit.depth, 3);
    assert.equal(hit.path.length, 3);
    assert.equal(hitFromMeta(null), null);
    assert.equal(hitFromMeta({source: {...meta.source!, node_id: null}} as unknown as FileMeta), null);
});

test("a query in one line", () => {
    assert.equal(
        compactQuery({
            providers: null,
            collections: ["ALPHA"],
            kind: ["EQUI"],
            depth: {min: 2},
            where: [{key: "TYPE", eq: "X"}, {key: "TAG", in: ["a", "b"]}, {key: "NAME", prefix: "P-"}],
            select: null,
            limit: 200,
            cursor: null,
        }),
        "collections ALPHA · kind EQUI · depth 2.. · TYPE = X · TAG in [a, b] · NAME starts P-",
    );
    assert.equal(compactQuery(null), "");
});
