// The element links on the client: the calls and the bodies they send, the Results section's
// labels, and the selection's nodes from saved-group members.

import assert from "node:assert/strict";
import { test } from "node:test";

// The API module reads the runtime config, which reads `window`.
const g = globalThis as Record<string, unknown>;
g.window ??= { location: new URL("http://localhost/"), addEventListener() {}, removeEventListener() {} };

// The panel realm stubs what the viewer's modules need of a browser (workers, the scene).
await import("../fixtures/panelRealm");

const { elementLinksApi, latestRun, queryElementLinks, elementLinks } = await import("@/services/api/elementLinks");
const { keyLabel, summaryParts } = await import("@/components/info_box_selected_object/ElementLinksSection");
const { nodeOfMember } = await import("@/utils/groups/selectionNodes");
const { PLUGIN_API_VERSION } = await import("@/plugins/registry");
const { VIEWER_CORE_API_VERSION } = await import("@/viewer-core");

type Link = import("@/services/api/elementLinks").ElementLink;

async function capture<T>(call: () => Promise<T>, answer: unknown = {}): Promise<{ url: string; method: string; body: unknown }> {
  const realFetch = g.fetch;
  let seen = { url: "", method: "GET", body: undefined as unknown };
  g.fetch = async (url: unknown, init?: { method?: string; body?: string }) => {
    seen = { url: String(url), method: init?.method ?? "GET", body: init?.body ? JSON.parse(init.body) : undefined };
    return new Response(JSON.stringify(answer), { status: 200, headers: { "Content-Type": "application/json" } });
  };
  try {
    await call();
  } finally {
    g.fetch = realFetch;
  }
  return seen;
}

test("the calls name their routes and send their bodies", async () => {
  const nodes = [{ key: "file:m.ifc#p:RACK" }];
  const q = await capture(() => queryElementLinks("user:me", { nodes, kinds: ["sim_run"], limit: 10 }));
  assert.match(q.url, /\/scopes\/user%3Ame\/elements\/links\/query$/);
  assert.equal(q.method, "POST");
  assert.deepEqual(q.body, { nodes, kinds: ["sim_run"], limit: 10 });

  const one = await capture(() => elementLinks("user:me", "file:m.ifc#p:RACK/B1", { kinds: ["weld", "connection"] }));
  assert.match(one.url, /\/elements\/file%3Am\.ifc%23p%3ARACK%2FB1\/links\?kinds=weld%2Cconnection$/);

  const ck = await capture(() => elementLinksApi.connection("user:me", "0123456789abcdef"));
  assert.match(ck.url, /\/connections\/0123456789abcdef$/);

  const batch = await capture(() =>
    elementLinksApi.startBatch("user:me", { group_id: "g1", provider: "p", policy: "failed", batch: { id: "b1" } }),
  );
  assert.match(batch.url, /\/simulations\/batch$/);
  assert.deepEqual(batch.body, { group_id: "g1", provider: "p", policy: "failed", batch: { id: "b1" } });

  const subjects = await capture(() => elementLinksApi.makeSubjects("user:me", { cks: ["0123456789abcdef"] }));
  assert.match(subjects.url, /\/elements\/subjects$/);
});

test("the Results section's labels", () => {
  assert.equal(keyLabel("file:m.ifc#p:RACK/BEAM%2F1"), "BEAM/1");
  assert.equal(keyLabel("node:prov/coll/n-42"), "n-42");
  assert.equal(keyLabel("file:m.ifc#g:2O2Fr"), "2O2Fr");
  assert.deepEqual(
    summaryParts({ kinds: { contact: 4, connection: 2 }, sim_runs: { done: 2, running: 0 }, stale: 1, orphan: 0 }),
    ["2 contacts", "2 connection links", "runs: 2 done", "1 stale"],
  );
  const row = (created_at: string, status: string): Link =>
    ({ kind: "sim_run", element_key: "k", doc_key: "d", created_at, data: { status }, stale: false, orphan: false, superseded: false }) as Link;
  assert.equal(latestRun({ rows: [row("2026-01-01", "error"), row("2026-01-02", "done")] })?.data.status, "done");
  assert.equal(latestRun({ rows: [] }), null);
});

test("a saved-group member is a node, with its provider's own id when the tree had one", () => {
  const target = { kind: "file" as const, source_key: "m.ifc" };
  assert.deepEqual(nodeOfMember({ target, element: "B1", path: ["RACK", "B1"] }), { target, element: "B1", path: ["RACK", "B1"] });
  const stored = {
    target,
    element: "B1",
    path: ["B1"],
    source: {
      kind: "stored" as const,
      scope: "user:me",
      provider: null,
      collection: null,
      subject: null,
      revision: null,
      key: "m.ifc",
      format: "ifc" as const,
      sha256: null,
      element_id: "e-1",
    },
  };
  assert.equal((nodeOfMember(stored) as { element_id?: string }).element_id, "e-1");
});

test("the plugin API has the per-element associations (1.36.0 or later)", () => {
  const atLeast = (v: string) => {
    const [maj, min] = v.split(".").map(Number);
    return maj > 1 || (maj === 1 && min >= 36);
  };
  assert.ok(atLeast(PLUGIN_API_VERSION));
  assert.equal(VIEWER_CORE_API_VERSION, PLUGIN_API_VERSION);
});
