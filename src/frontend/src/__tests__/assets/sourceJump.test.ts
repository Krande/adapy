import assert from "node:assert/strict";
import test from "node:test";

import type {FileMeta, FileMetaElement} from "@/services/api/searchExport";

// `scopeStore` persists through `sessionStorage` at module load, and the API types sit next to
// modules that read it too, so the browser globals are stubbed BEFORE the dynamic imports -- as in
// sweepTracking.test.ts.
const memory = new Map<string, string>();
const storage = {
    getItem: (k: string): string | null => memory.get(k) ?? null,
    setItem: (k: string, v: string): void => void memory.set(k, String(v)),
    removeItem: (k: string): void => void memory.delete(k),
    clear: (): void => memory.clear(),
    key: (): string | null => null,
    get length(): number {
        return memory.size;
    },
};
const globals = globalThis as unknown as {sessionStorage: unknown; localStorage: unknown};
globals.sessionStorage = storage;
globals.localStorage = storage;

const {decideScope, normaliseName, sourceJumpFor} = await import("@/assets/sourceJump");
const {scopeUrlPart} = await import("@/state/scopeStore");

const SCOPE = "project:p-alpha";
const set = (node_id: string, full_name: string, extra: Partial<FileMetaElement["source"]> = {}) => ({
    provider: "vendor",
    scope: SCOPE,
    collection: "ALPHA",
    node_id,
    full_name,
    source_ref: null,
    ...extra,
});

const ELEMENTS: FileMetaElement[] = [
    {name: "/SITE-A", guid: "g-site", role: "level", source: set("fn-site", "/SITE-A")},
    {name: "/ZONE-A", guid: "g-zone", role: "level", source: set("fn-zone", "/ZONE-A")},
    {name: "STRU-1", guid: "g-hit", role: "hit", source: set("fn-hit", "/STRU-1")},
    {name: "BM-1", guid: "g-bm", role: "member", source: set("fn-bm", "/BM-1", {source_ref: "=1/4"})},
];

const meta = (extra: Partial<FileMeta> = {}): FileMeta => ({
    schema: "ada.files/meta@1",
    file: "out/ALPHA/STRU-1.ifc",
    format: "ifc",
    created_at: null,
    created_by: null,
    source: {
        provider: "vendor",
        scope: SCOPE,
        collection: "ALPHA",
        node_id: "fn-hit",
        full_name: "/STRU-1",
        source_ref: null,
        revision: "r1",
        path: [
            {node_id: "fn-site", full_name: "/SITE-A", kind: "SITE", depth: 1},
            {node_id: "fn-zone", full_name: "/ZONE-A", kind: "ZONE", depth: 2},
        ],
    },
    query: null,
    matched: null,
    run_id: "run-1",
    attributes: null,
    derived: null,
    elements: ELEMENTS,
    origin: "sidecar",
    ...extra,
});

// The viewer's rows, NEAREST FIRST: a member under an unmatched sub-assembly under the hit.
const memberChain = [
    {id: "n5", name: "BM-1"},
    {id: "n4", name: "/FRMW-1"},
    {id: "n3", name: "STRU-1"},
    {id: "n2", name: "/ZONE-A"},
    {id: "n1", name: "/SITE-A"},
];

test("a member with its own set: its node, the levels and the hit above it as the path", () => {
    const jump = sourceJumpFor(memberChain, meta())!;
    assert.equal(jump.via, "self");
    assert.equal(jump.scope, SCOPE);
    assert.equal(jump.hit.node_id, "fn-bm");
    assert.equal(jump.hit.provider, "vendor");
    assert.equal(jump.hit.collection, "ALPHA");
    assert.equal(jump.hit.source_ref, "=1/4");
    assert.deepEqual(
        jump.hit.path.map((p) => p.node_id),
        ["fn-site", "fn-zone", "fn-hit"],
    );
    assert.equal(jump.hit.parent_id, "fn-hit");
});

test("an element without a set takes its nearest ancestor's", () => {
    const jump = sourceJumpFor(memberChain.slice(1), meta())!;
    assert.equal(jump.via, "ancestor");
    assert.equal(jump.element, "STRU-1");
    assert.equal(jump.hit.node_id, "fn-hit");
    assert.deepEqual(
        jump.hit.path.map((p) => p.node_id),
        ["fn-site", "fn-zone"],
    );
});

test("names match across the leading slash and the export's [id] suffix; GlobalId wins", () => {
    assert.equal(normaliseName(" /ZONE-A [fn-zone] "), "ZONE-A");
    const jump = sourceJumpFor([{id: "x", name: "ZONE-A [fn-zone]"}, {id: "y", name: "SITE-A"}], meta())!;
    assert.equal(jump.hit.node_id, "fn-zone");
    assert.deepEqual(jump.hit.path.map((p) => p.node_id), ["fn-site"]);
    const byGuid = sourceJumpFor([{id: "g-bm", name: "renamed"}], meta())!;
    assert.equal(byGuid.hit.node_id, "fn-bm");
});

test("an older record without per-element sets: levels and hit by full name, else the file's hit", () => {
    const old = meta({elements: undefined});
    const atHit = sourceJumpFor([{id: "n3", name: "/STRU-1"}, {id: "n2", name: "/ZONE-A"}], old)!;
    assert.equal(atHit.hit.node_id, "fn-hit");
    assert.deepEqual(atHit.hit.path.map((p) => p.node_id), ["fn-zone"]);
    // Nothing named alike in the tree: the record's own hit, with its own path.
    const file = sourceJumpFor([{id: "q", name: "unrelated"}], old)!;
    assert.equal(file.via, "file");
    assert.equal(file.hit.node_id, "fn-hit");
    assert.deepEqual(file.hit.path.map((p) => p.node_id), ["fn-site", "fn-zone"]);
    assert.equal(file.scope, SCOPE);
});

test("no record, or one naming no source node: nothing to offer", () => {
    assert.equal(sourceJumpFor(memberChain, null), null);
    assert.equal(sourceJumpFor(memberChain, meta({elements: [], source: null})), null);
});

test("an element's own scope wins over the record's", () => {
    const elements = ELEMENTS.map((e) => (e.name === "BM-1" ? {...e, source: {...e.source, scope: "shared"}} : e));
    assert.equal(sourceJumpFor(memberChain, meta({elements}))!.scope, "shared");
});

test("scope: stay, switch to a scope the user can open, refuse one they cannot", () => {
    const available = [
        {kind: "user" as const, id: "me", name: "My files"},
        {kind: "shared" as const, id: null, name: "Shared"},
        {kind: "project" as const, id: "p-alpha", name: "Alpha"},
    ];
    assert.deepEqual(decideScope(null, "shared", available, scopeUrlPart), {kind: "stay"});
    assert.deepEqual(decideScope("shared", "shared", available, scopeUrlPart), {kind: "stay"});
    const sw = decideScope(SCOPE, "user:me", available, scopeUrlPart);
    assert.equal(sw.kind, "switch");
    assert.equal(sw.kind === "switch" && sw.to.id, "p-alpha");
    const no = decideScope("project:p-beta", "shared", available, scopeUrlPart);
    assert.equal(no.kind, "refuse");
    assert.match(no.kind === "refuse" ? no.reason : "", /project p-beta.*do not have access/);
});
