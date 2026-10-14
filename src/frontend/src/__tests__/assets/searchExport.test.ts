import assert from "node:assert/strict";
import test from "node:test";

import {
    compactQuery,
    docFromMeta,
    exportFolderCandidates,
    exportQuery,
    exportTargetScopes,
    folderSuggestions,
    galleryFolderOrder,
    galleryStops,
    geometryPrompt,
    hitFromMeta,
    jointNameMatches,
    loginSummary,
    isExportSidecar,
    mapLimited,
    mergeMeshPreflights,
    mergePreflights,
    meshPrompt,
    metaSidecarKey,
    normalizeFolder,
    queryForCollection,
    selectedTotal,
    stopOfAdjacentFile,
    thumbSidecarKey,
} from "@/assets/searchExport";
import type {SearchQuery} from "@/services/api/attributeSearch";
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

test("gallery stops: a merged file is one stop per hit, with that hit's thumbnail", () => {
    const joint = (name: string, thumb: string | null) => ({node_id: name, full_name: `/${name}`, name, thumb});
    const doc = {
        files: [
            {
                file: "e/alpha.glb",
                collection: "alpha",
                provider: "p",
                node_id: "",
                full_name: "alpha",
                meta: "",
                thumb: "e/alpha.thumb.png",
                hits: 2,
                joints: [joint("J1", "e/alpha.j0000.thumb.png"), joint("J2", null)],
            },
            {file: "e/beta.glb", collection: "beta", provider: "p", node_id: "", full_name: "beta", meta: "", thumb: "e/beta.thumb.png"},
        ],
    };
    const stops = galleryStops(["e/alpha.glb", "e/beta.glb", "e/hand.glb"], doc);
    assert.deepEqual(
        stops.map((s) => [s.file, s.fileIndex, s.joint?.name ?? null, s.jointIndex, s.joints, s.thumb]),
        [
            ["e/alpha.glb", 0, "J1", 0, 2, "e/alpha.j0000.thumb.png"],
            ["e/alpha.glb", 0, "J2", 1, 2, null],
            ["e/beta.glb", 1, null, 0, 0, "e/beta.thumb.png"],
            // Not in the doc: no word on its thumbnail (the caller looks beside the file).
            ["e/hand.glb", 2, null, 0, 0, undefined],
        ],
    );
    assert.equal(new Set(stops.map((s) => s.id)).size, stops.length);
    // No doc: one stop per file.
    assert.deepEqual(galleryStops(["a.glb"], null).map((s) => s.joint), [null]);

    // "Next file" lands on that file's first hit, wrapping; "previous" likewise.
    assert.equal(stopOfAdjacentFile(stops, 1, 1), 2);
    assert.equal(stopOfAdjacentFile(stops, 3, 1), 0);
    assert.equal(stopOfAdjacentFile(stops, 2, -1), 0);
    assert.equal(stopOfAdjacentFile(stops, 0, -1), 3);
    assert.equal(stopOfAdjacentFile([], 0, 1), 0);
});

test("an export index is looked for in the file's own folder first, then above", () => {
    assert.deepEqual(exportFolderCandidates("e/run/alpha/a.ifc"), ["e/run/alpha", "e/run", "e"]);
    assert.deepEqual(exportFolderCandidates("e/run/x/y/z/a.glb"), ["e/run/x/y/z", "e/run/x/y", "e/run/x"]);
    assert.deepEqual(exportFolderCandidates("a.glb"), []);
});

test("a merged file's sidecar stands in for a missing index; one without hits does not", () => {
    const joints = [{node_id: "1", full_name: "/J1", name: "J1", thumb: "e/alpha.j0000.thumb.png"}];
    const meta = {
        source: {provider: "p", collection: "alpha", node_id: null, full_name: "alpha"},
        derived: {glb: "e/alpha.glb", step: null, thumb: "e/alpha.thumb.png"},
        joints,
    } as unknown as FileMeta;
    const doc = docFromMeta("e/alpha.glb", meta)!;
    assert.equal(doc.files.length, 1);
    assert.deepEqual(galleryStops(["e/alpha.glb"], doc).map((s) => [s.joint?.name, s.thumb]), [["J1", "e/alpha.j0000.thumb.png"]]);
    assert.equal(docFromMeta("e/a.glb", {...meta, joints: []} as FileMeta), null);
    assert.equal(docFromMeta("e/a.glb", null), null);
});

test("a hit is found in the tree by name, ignoring a leading slash and then case", () => {
    const j = {name: "Part-1 [n7]", full_name: "/SITE/Part-1"};
    assert.ok(jointNameMatches("Part-1 [n7]", j));
    assert.ok(jointNameMatches("SITE/Part-1", j));
    assert.ok(jointNameMatches(" /site/part-1 ", j));
    assert.ok(!jointNameMatches("Part-1", j));
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
    assert.equal(
        compactQuery({
            providers: null,
            collections: null,
            exclude_collections: ["BETA"],
            exclude_providers: ["prov-b"],
            kind: null,
            depth: null,
            where: [{key: "A", ne: 1}, {key: "B", not_in: ["x"]}, {key: "C", exists: true}, {key: "D", missing: true}],
            select: null,
            limit: 200,
            cursor: null,
        }),
        "not collections BETA · not providers prov-b · A != 1 · B not in [x] · has C · no D",
    );
    assert.equal(compactQuery(null), "");
});

// --- leaving collections out of an export (clicking their counts) ----------------------------------

const Q: SearchQuery = {
    providers: null,
    collections: null,
    exclude_collections: ["old"],
    kind: null,
    depth: null,
    where: [{key: "TYPE", eq: "STRU"}],
    select: null,
    limit: 200,
    cursor: null,
};

test("deselected collections join the query's exclusions, and the total drops by their counts", () => {
    const out = new Set(["alpha", "gamma"]);
    const q = exportQuery(Q, out);
    assert.deepEqual(q.exclude_collections, ["old", "alpha", "gamma"]);
    assert.deepEqual(q.where, Q.where, "the filters are the search's own");
    assert.equal(exportQuery(Q, new Set()), Q, "nothing left out sends the query untouched");
    const counts = [
        {collection: "alpha", count: 300},
        {collection: "beta", count: 200},
        {collection: "gamma", count: 100},
    ];
    assert.equal(selectedTotal(counts, out), 200);
    assert.equal(selectedTotal(counts, new Set()), 600);
});

test("an include list loses the deselected collections too, ignoring case as the server does", () => {
    const q = exportQuery({...Q, collections: ["ALPHA", "beta"], exclude_collections: null}, new Set(["alpha"]));
    assert.deepEqual(q.collections, ["beta"]);
    assert.deepEqual(q.exclude_collections, ["alpha"]);
});

// --- the mesh export's preflight prompt --------------------------------------------------------------

test("the mesh prompt names the collections with no tree, offers their request and what is ready", () => {
    const p = meshPrompt({
        geometry: "mesh",
        hits: 120,
        ready: 20,
        no_tree: 95,
        unknown: 5,
        collections: [
            {provider: "vendor", collection: "delta", hits: 25, ready: 20, no_tree: 0, unknown: 5, unknown_reason: "not in the tree", unknown_examples: ["/X"]},
            {provider: "vendor", collection: "alpha", hits: 60, ready: 0, no_tree: 60, unknown: 0, unknown_reason: null, unknown_examples: []},
            {provider: "vendor", collection: "gamma", hits: 35, ready: 0, no_tree: 35, unknown: 0, unknown_reason: null, unknown_examples: []},
        ],
        tree_requests: [
            {
                provider: "vendor",
                collections: ["alpha", "gamma"],
                available: true,
                allowed: true,
                reason: null,
                label: "Request project tree (vendor)",
                plugin_id: "vendor-plugin",
                sweep_running: null,
            },
        ],
        took_ms: 3,
    });
    assert.equal(p.ask, true);
    assert.equal(p.headline, "2 of 3 collections have no project tree from vendor yet -- 95 of 120 hits; 5 hits are not in their tree");
    assert.equal(p.ready, 20);
    assert.deepEqual(p.readyCollections, ["delta"]);
    assert.deepEqual(p.emptyCollections, ["alpha", "gamma"]);
    assert.deepEqual(p.requests, [
        {provider: "vendor", collections: ["alpha", "gamma"], label: "Request project tree (vendor)", cannot: null, sweepNote: null},
    ]);
    assert.equal(p.unknown[0].collection, "delta");
});

test("a mesh prompt says why the tree request is not this user's, and nothing asks when all is ready", () => {
    const base = {geometry: "mesh" as const, took_ms: 1, unknown: 0};
    const denied = meshPrompt({
        ...base,
        hits: 1,
        ready: 0,
        no_tree: 1,
        collections: [{provider: "vendor", collection: "alpha", hits: 1, ready: 0, no_tree: 1, unknown: 0, unknown_reason: null, unknown_examples: []}],
        tree_requests: [
            {
                provider: "vendor",
                collections: ["alpha"],
                available: true,
                allowed: false,
                reason: "the 'vendor-plugin' job is restricted to administrators",
                label: null,
                plugin_id: "vendor-plugin",
                sweep_running: {id: "s1", created_at: null},
            },
        ],
    });
    assert.equal(denied.requests[0].cannot, "the 'vendor-plugin' job is restricted to administrators");
    assert.match(denied.requests[0].sweepNote ?? "", /vendor sweep is running/);
    assert.equal(denied.requests[0].label, "Request project trees (vendor)");
    const fine = meshPrompt({
        ...base,
        hits: 2,
        ready: 2,
        no_tree: 0,
        collections: [{provider: "vendor", collection: "delta", hits: 2, ready: 2, no_tree: 0, unknown: 0, unknown_reason: null, unknown_examples: []}],
        tree_requests: [],
    });
    assert.equal(fine.ask, false);
    assert.equal(fine.headline, null);
});

// --- the preflight, a collection at a time ----------------------------------------------------------

test("a per-collection check narrows the query to that collection and keeps the rest", () => {
    const q = queryForCollection({...Q, collections: ["alpha", "beta"]}, "beta");
    assert.deepEqual(q.collections, ["beta"]);
    assert.deepEqual(q.exclude_collections, Q.exclude_collections);
    assert.deepEqual(q.where, Q.where);
});

test("the checks run a few at a time, report each one done, and answer in order", async () => {
    let running = 0;
    let most = 0;
    const seen: number[] = [];
    const out = await mapLimited(
        [5, 1, 4, 2, 3],
        2,
        async (n) => {
            running += 1;
            most = Math.max(most, running);
            await new Promise((r) => setTimeout(r, n));
            running -= 1;
            return n * 10;
        },
        (done) => seen.push(done),
    );
    assert.deepEqual(out, [50, 10, 40, 20, 30]);
    assert.equal(most, 2);
    assert.deepEqual(seen, [1, 2, 3, 4, 5]);
});

test("per-collection mesh checks add up to the one check of them all", () => {
    const part = (collection: string, ready: number, noTree: number, sweep: {id: string; created_at: null} | null = null) => ({
        geometry: "mesh" as const,
        hits: ready + noTree,
        ready,
        no_tree: noTree,
        unknown: 0,
        collections: [{provider: "vendor", collection, hits: ready + noTree, ready, no_tree: noTree, unknown: 0, unknown_reason: null, unknown_examples: []}],
        tree_requests: noTree
            ? [{provider: "vendor", collections: [collection], available: true, allowed: true, reason: null, label: "Tree", plugin_id: "p", sweep_running: sweep}]
            : [],
        took_ms: ready,
    });
    const all = mergeMeshPreflights([part("alpha", 3, 0), part("beta", 0, 4), part("gamma", 0, 2, {id: "s1", created_at: null})]);
    assert.deepEqual([all.hits, all.ready, all.no_tree], [9, 3, 6]);
    assert.deepEqual(all.collections.map((c) => c.collection), ["alpha", "beta", "gamma"]);
    assert.equal(all.tree_requests.length, 1);
    assert.deepEqual(all.tree_requests[0].collections, ["beta", "gamma"]);
    assert.deepEqual(all.tree_requests[0].sweep_running, {id: "s1", created_at: null});
});

test("per-collection design checks add up too", () => {
    const part = (collection: string, ready: number, missing: number): ExportPreflight => ({
        geometry_provider: "design",
        hits: ready + missing,
        ready,
        missing,
        unknown: 0,
        jobs: missing ? 1 : 0,
        collections: [{collection, hits: ready + missing, ready, missing, listed: missing, unpublished: 0, unknown: 0, jobs: missing ? 1 : 0, missing_reason: null, unknown_reason: null, unknown_examples: []}],
        request: {available: true, allowed: true, reason: null, plugin_id: "p", label: "Request", per_job: 500},
        sweep_running: null,
        took_ms: 1,
    });
    const all = mergePreflights([part("alpha", 2, 1), part("beta", 0, 5)]);
    assert.deepEqual([all.hits, all.ready, all.missing, all.jobs], [8, 2, 6, 2]);
    assert.equal(all.collections.length, 2);
});
