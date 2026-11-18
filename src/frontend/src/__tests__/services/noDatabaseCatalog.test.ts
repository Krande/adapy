import assert from "node:assert/strict";
import test from "node:test";

// A local viewer: REST, no database (the page's config.js sets ADA_NO_DATABASE). The
// browser globals are stubbed BEFORE the dynamic imports: `services/auth/oidc.ts` reads
// `sessionStorage` at module load, and the API base and transport are read off `window`.
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
const win = {
    location: {origin: "https://app.example.test", pathname: "/", search: ""},
    COMMS_MODE: "rest",
    ADA_NO_DATABASE: true,
} as Record<string, unknown>;
(globalThis as unknown as {window: unknown}).window = win;

const TYPE_ID = "11111111-2222-3333-4444-555555555555";
const detail = (lx: number) => ({
    id: TYPE_ID,
    slug: "meshed",
    name: "Meshed",
    description: null,
    doc: {bbox: {lx, ly: 2, lz: 3}, mass: 1000, ifc_element_class: "IfcBuildingElementProxy", ports: []},
    cad_key: `_equipment/${TYPE_ID}/source.stl`,
    revision: 0,
    created_by: null,
    updated_at: null,
    preview_glb_key: null,
});

/** Every request the code under test sends, as `<METHOD> <path>`. */
const requests: string[] = [];
let inferred = false;
globalThis.fetch = (async (input: unknown, init?: RequestInit) => {
    const url = new URL(String(input), "https://app.example.test");
    const method = (init?.method ?? "GET").toUpperCase();
    requests.push(`${method} ${url.pathname}`);
    const json = (body: unknown, status = 200) =>
        new Response(JSON.stringify(body), {status, headers: {"content-type": "application/json"}});
    if (method === "POST" && url.pathname.endsWith("/infer-bbox")) {
        inferred = true;
        return json({job_id: null, status: "done", bbox: {lx: 1, ly: 2, lz: 3}, derived_key: "k"});
    }
    if (method === "GET" && url.pathname.endsWith(`/equipment-types/${TYPE_ID}`)) return json(detail(inferred ? 1 : 0));
    return json({detail: "unexpected"}, 500);
}) as typeof fetch;

const {conversionApi} = await import("@/services/api/conversion");
const {useEquipmentCatalogStore} = await import("@/state/equipmentCatalogStore");

test("without a database the audit rows and my-jobs are not asked for", async () => {
    requests.length = 0;
    assert.deepEqual(await conversionApi.myJobs("shared"), []);
    const jobId = await conversionApi.auditLocalCreate("shared", {key: "a.ifc", target_format: "glb"});
    assert.equal(jobId, "");
    await conversionApi.auditLocalUpdate("shared", jobId, {status: "done"});
    await conversionApi.auditLocalUpdate("shared", "wasm-x", {status: "cancelled"});
    assert.deepEqual(requests, []);
});

test("a bbox the server inferred in the request is done at once, with no job to poll", async () => {
    requests.length = 0;
    useEquipmentCatalogStore.setState({
        selectedEquipmentId: TYPE_ID,
        equipmentDraft: detail(0),
        equipmentDirty: false,
        bboxJob: null,
        equipmentError: null,
    });
    await useEquipmentCatalogStore.getState().inferBbox();
    const s = useEquipmentCatalogStore.getState();
    assert.equal(s.equipmentError, null);
    assert.equal(s.bboxJob?.status, "done");
    assert.equal(s.equipmentDraft?.doc.bbox.lx, 1); // the refetched, inferred doc
    // The inference, then the refetch of the draft: no job-status poll.
    assert.deepEqual(
        requests.map((r) => r.replace(/\/api\/scopes\/[^/]+/, "")),
        [`POST /equipment-types/${TYPE_ID}/infer-bbox`, `GET /equipment-types/${TYPE_ID}`],
    );
});

test("a refused inference shows its reason and clears the running state", async () => {
    globalThis.fetch = (async () =>
        new Response(JSON.stringify({detail: "bbox inference unavailable here"}), {
            status: 503,
            headers: {"content-type": "application/json"},
        })) as typeof fetch;
    useEquipmentCatalogStore.setState({equipmentDraft: detail(0), bboxJob: null, equipmentError: null});
    await useEquipmentCatalogStore.getState().inferBbox();
    const s = useEquipmentCatalogStore.getState();
    assert.equal(s.bboxJob, null);
    assert.match(String(s.equipmentError), /unavailable here/);
});
